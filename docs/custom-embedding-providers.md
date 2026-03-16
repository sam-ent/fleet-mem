# Custom Embedding Providers

fleet-mem supports any embedding provider through a simple plugin interface. If your provider is not covered by the built-in Ollama or OpenAI-compatible adapters, you can create your own in under 30 lines of code.

## The interface

All embedding providers implement four methods defined in `src/embedding/base.py`:

```python
from abc import ABC, abstractmethod

class Embedding(ABC):

    def embed(self, text: str) -> list[float]:
        """Embed a single text string. Return a list of floats."""

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts. Handle chunking if the API has batch limits."""

    def get_dimension(self) -> int:
        """Return the embedding vector dimension (e.g. 768, 1536)."""

    def get_provider(self) -> str:
        """Return a human-readable provider name (e.g. 'cohere/embed-v3')."""
```

That is the entire contract. fleet-mem does not care how the vectors are produced, only that they come back as lists of floats with a consistent dimension.

## Step-by-step guide

### 1. Create your adapter file

Create a new file in `src/embedding/`. Here is a minimal template:

```python
# src/embedding/my_provider.py
import os
from src.embedding.base import Embedding

BATCH_SIZE = 64  # adjust to your API's batch limit

class MyProviderEmbedding(Embedding):

    def __init__(self, api_key: str | None = None, model: str = "default-model"):
        self._api_key = api_key or os.environ.get("MY_PROVIDER_API_KEY", "")
        self._model = model
        self._dimension: int | None = None
        # Initialize your SDK client here

    def embed(self, text: str) -> list[float]:
        # Call your provider's API for a single text
        vector = [...]  # your API call here
        if self._dimension is None:
            self._dimension = len(vector)
        return vector

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            chunk = texts[i : i + BATCH_SIZE]
            # Call your provider's batch API
            embeddings = [...]  # your API call here
            results.extend(embeddings)
            if self._dimension is None and embeddings:
                self._dimension = len(embeddings[0])
        return results

    def get_dimension(self) -> int:
        if self._dimension is None:
            self.embed("dimension probe")
        return self._dimension

    def get_provider(self) -> str:
        return f"my-provider/{self._model}"
```

### 2. Register it in the server

Edit `src/server.py` and find the `_get_embedder()` function. Add your provider:

```python
def _get_embedder(config=None):
    cfg = config or _get_config()
    if cfg.embedding_provider == "my-provider":
        from .embedding.my_provider import MyProviderEmbedding
        return MyProviderEmbedding(api_key=cfg.embed_api_key)
    elif cfg.embedding_provider == "openai-compat":
        ...
```

### 3. Set your environment variables

```bash
EMBEDDING_PROVIDER=my-provider
MY_PROVIDER_API_KEY=your-key-here
```

### 4. Test it

```bash
.venv/bin/pytest tests/ -v  # existing tests still pass
# Then index a small project to verify end-to-end:
# Use the MCP tool: index_codebase(path="/path/to/small/repo")
```

## Real-world examples

### Cohere

```python
# src/embedding/cohere_embed.py
import cohere
from src.embedding.base import Embedding

class CohereEmbedding(Embedding):

    def __init__(self, api_key: str, model: str = "embed-english-v3.0"):
        self._client = cohere.ClientV2(api_key)
        self._model = model
        self._dimension: int | None = None

    def embed(self, text: str) -> list[float]:
        r = self._client.embed(
            texts=[text], model=self._model, input_type="search_document"
        )
        self._dimension = len(r.embeddings[0])
        return r.embeddings[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        # Cohere supports up to 96 texts per call
        results = []
        for i in range(0, len(texts), 96):
            chunk = texts[i : i + 96]
            r = self._client.embed(
                texts=chunk, model=self._model, input_type="search_document"
            )
            results.extend(r.embeddings)
        if self._dimension is None and results:
            self._dimension = len(results[0])
        return results

    def get_dimension(self) -> int:
        if self._dimension is None:
            self.embed("probe")
        return self._dimension

    def get_provider(self) -> str:
        return f"cohere/{self._model}"
```

### AWS Bedrock (Titan)

```python
# src/embedding/bedrock_embed.py
import json
import boto3
from src.embedding.base import Embedding

class BedrockEmbedding(Embedding):

    def __init__(self, model: str = "amazon.titan-embed-text-v2:0", region: str = "us-east-1"):
        self._client = boto3.client("bedrock-runtime", region_name=region)
        self._model = model
        self._dimension: int | None = None

    def embed(self, text: str) -> list[float]:
        response = self._client.invoke_model(
            modelId=self._model,
            body=json.dumps({"inputText": text}),
        )
        vector = json.loads(response["body"].read())["embedding"]
        if self._dimension is None:
            self._dimension = len(vector)
        return vector

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        # Titan does not support batch; call one at a time
        return [self.embed(t) for t in texts]

    def get_dimension(self) -> int:
        if self._dimension is None:
            self.embed("probe")
        return self._dimension

    def get_provider(self) -> str:
        return f"bedrock/{self._model}"
```

### Hugging Face (local via sentence-transformers)

```python
# src/embedding/hf_embed.py
from src.embedding.base import Embedding

class HuggingFaceEmbedding(Embedding):

    def __init__(self, model: str = "all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer
        self._model_name = model
        self._model = SentenceTransformer(model)
        self._dimension = self._model.get_sentence_embedding_dimension()

    def embed(self, text: str) -> list[float]:
        return self._model.encode(text).tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts).tolist()

    def get_dimension(self) -> int:
        return self._dimension

    def get_provider(self) -> str:
        return f"huggingface/{self._model_name}"
```

## Important notes

- **Dimension consistency**: once you index a project with one embedding model, you cannot switch models without re-indexing. Different models produce different dimensions and vector spaces.
- **Batch limits**: check your provider's documentation for maximum batch sizes. Chunking in `embed_batch` prevents API errors.
- **Error handling**: consider wrapping API calls in try/except and raising `ConnectionError` with a clear message, following the pattern in `src/embedding/ollama_embed.py`.
- **Dependencies**: add your provider's SDK to `pyproject.toml` under `[project.optional-dependencies]` rather than core dependencies, so users who don't need it aren't forced to install it.
