# Observability Library

Python library for sending logs to Loki and visualizing them in Grafana.

## Description

Asynchronous logging handler that integrates with Python's standard logging module to send logs to Loki using aiohttp for non-blocking HTTP requests.

## Components

**LokiHandler**
- Async logging handler compatible with Python's logging module
- Non-blocking HTTP requests to Loki
- Customizable labels for log filtering in Grafana
- Bearer token authentication support
- Configurable timeout for HTTP requests

**LokiLoggerLabels**
- Pydantic schema for label validation
- Standardizes label format across applications
- Validates project names and environment values

## Requirements

- Python 3.10 or higher
- aiohttp >= 3.8.0
- pydantic
- Running async event loop

## Installation

```bash
pip install observability-library
```

From repository:

```bash
pip install git+https://github.com/aquintero/observability-library.git
```

## Configuration Parameters

**LokiHandler**
- `url` (required): Loki endpoint URL
- `labels` (optional): Dictionary of labels for Grafana
- `timeout` (optional): HTTP request timeout in seconds (default: 5)
- `auth_token` (optional): Bearer token for authentication

**High memory usage**

Solution: Call `loki_handler.close()` on shutdown.

## License

MIT
