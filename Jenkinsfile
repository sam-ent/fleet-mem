pipeline {
    agent none

    triggers {
        pollSCM('H/2 * * * *')
    }

    stages {
        stage('Lint') {
            agent {
                docker { image 'python:3.13-slim' }
            }
            steps {
                sh '''
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
                        values '3.11', '3.12', '3.13'
                    }
                }
                agent {
                    docker { image "python:${PYTHON_VERSION}-slim" }
                }
                stages {
                    stage('Test') {
                        steps {
                            sh '''
                                python --version
                                pip install -e ".[dev]" -q
                                pytest tests/ -v
                            '''
                        }
                    }
                }
            }
        }

        stage('Install Smoke') {
            agent {
                docker { image 'python:3.13-slim' }
            }
            steps {
                sh '''
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
            agent any
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
        success {
            node('') {
                withCredentials([string(credentialsId: 'github-token', variable: 'GH_TOKEN')]) {
                    sh '''
                        curl -sf -X POST \
                          -H "Authorization: token $GH_TOKEN" \
                          -H "Accept: application/vnd.github+json" \
                          -H "Content-Type: application/json" \
                          "https://api.github.com/repos/sam-ent/fleet-mem/statuses/$GIT_COMMIT" \
                          -d "$(printf '{"state":"success","context":"jenkins/ci","description":"Build passed","target_url":"%s"}' "$BUILD_URL")"
                    '''
                }
            }
        }
        failure {
            node('') {
                withCredentials([string(credentialsId: 'github-token', variable: 'GH_TOKEN')]) {
                    sh '''
                        curl -sf -X POST \
                          -H "Authorization: token $GH_TOKEN" \
                          -H "Accept: application/vnd.github+json" \
                          -H "Content-Type: application/json" \
                          "https://api.github.com/repos/sam-ent/fleet-mem/statuses/$GIT_COMMIT" \
                          -d "$(printf '{"state":"failure","context":"jenkins/ci","description":"Build failed","target_url":"%s"}' "$BUILD_URL")"
                    '''
                }
            }
        }
    }
}
