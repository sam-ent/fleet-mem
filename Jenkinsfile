pipeline {
    agent any

    triggers {
        pollSCM('H/2 * * * *')
    }

    environment {
        PYENV_ROOT = '/opt/pyenv'
        PATH = "/opt/pyenv/bin:/opt/pyenv/shims:${env.PATH}"
    }

    stages {
        stage('Lint') {
            steps {
                sh '''
                    python3 -m venv .venv-lint
                    . .venv-lint/bin/activate
                    pip install -e ".[dev]" -q
                    ruff check fleet_mem/ tests/
                    ruff format --check fleet_mem/ tests/
                '''
            }
        }

        stage('Test Matrix') {
            matrix {
                axes {
                    axis {
                        name 'PYTHON_VERSION'
                        values '3.11.13', '3.12.10', '3.13'
                    }
                }
                stages {
                    stage('Test') {
                        steps {
                            sh """
                                if [ "\${PYTHON_VERSION}" = "3.13" ]; then
                                    PYTHON=python3
                                else
                                    eval "\$(pyenv init -)"
                                    pyenv shell \${PYTHON_VERSION}
                                    PYTHON=python
                                fi
                                \$PYTHON -m venv .venv-\${PYTHON_VERSION}
                                . .venv-\${PYTHON_VERSION}/bin/activate
                                python --version
                                pip install -e ".[dev]" -q
                                pytest tests/ -v
                            """
                        }
                    }
                }
            }
        }

        stage('Install Smoke') {
            steps {
                sh '''
                    python3 -m venv .venv-smoke
                    . .venv-smoke/bin/activate
                    pip install build -q
                    python -m build --wheel
                    pip install dist/*.whl
                    python -c "from fleet_mem.server import main; print('base install OK')"
                    python -c "from fleet_mem.observability import configure_logging; configure_logging(); print('structlog OK')"
                    python -c "from fleet_mem.fleet.sessions import register_agent; print('sessions OK')"
                '''
            }
        }

        stage('Docker Smoke') {
            steps {
                sh '''
                    docker build -t fleet-mem-ci .
                    docker run --rm fleet-mem-ci python -c "
from fleet_mem.server import main; print('server OK')
from fleet_mem.observability import configure_logging; configure_logging(); print('observability OK')
from fleet_mem.fleet.sessions import register_agent; print('sessions OK')
"
                '''
            }
        }
    }

    post {
        always {
            cleanWs()
        }
        success {
            script {
                if (env.CHANGE_ID) {
                    // PR build
                    githubNotify context: 'jenkins/ci', status: 'SUCCESS', description: 'Build passed'
                } else {
                    githubNotify context: 'jenkins/ci', status: 'SUCCESS', description: 'Build passed'
                }
            }
        }
        failure {
            script {
                githubNotify context: 'jenkins/ci', status: 'FAILURE', description: 'Build failed'
            }
        }
    }
}
