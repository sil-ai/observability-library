# Observability Library

Python library for sending logs to Loki and visualizing them in Grafana.

## Installation

```bash
pip install observability-library
```

Or from the repository:

```bash
pip install git+https://github.com/aquintero/observability-library.git
```

## Usage

```python
import logging
from observability_library import LokiHandler

logger = logging.getLogger("my-app")
logger.setLevel(logging.INFO)

loki_handler = LokiHandler(
    url="http://localhost:3100/loki/api/v1/push",
    labels={
        "app": "my-application",
        "env": "production"
    }
)

logger.addHandler(loki_handler)

logger.info("This log will be sent to Loki")
logger.error("Error detected in application")
```

## Configuration

### LokiHandler Parameters

- `url`: Loki endpoint URL (required)
- `labels`: Dictionary of labels for filtering in Grafana (optional)
- `timeout`: Timeout for HTTP requests in seconds (default: 5)

### Example with Custom Labels

```python
loki_handler = LokiHandler(
    url="http://localhost:3100/loki/api/v1/push",
    labels={
        "app": "backend",
        "env": "staging",
        "service": "api",
        "region": "us-east-1"
    },
    timeout=10
)
```

## License

MIT
