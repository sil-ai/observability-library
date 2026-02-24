import logging
import json
import time
from typing import Dict, Optional
import requests


class LokiHandler(logging.Handler):
    """
    Logging handler to send logs to Loki/Grafana.
    """

    def __init__(
        self,
        url: str,
        labels: Optional[Dict[str, str]] = None,
        timeout: int = 5,
    ):
        """
        Args:
            url: Loki endpoint URL (e.g., http://localhost:3100/loki/api/v1/push)
            labels: Dictionary of labels for Loki (e.g., {"app": "my-app", "env": "prod"})
            timeout: Timeout for HTTP requests in seconds
        """
        super().__init__()
        self.url = url
        self.labels = labels or {}
        self.timeout = timeout

    def emit(self, record: logging.LogRecord) -> None:
        """
        Sends a log record to Loki.
        """
        try:
            log_entry = self.format_record(record)
            self.send_to_loki(log_entry)
        except Exception:
            self.handleError(record)

    def format_record(self, record: logging.LogRecord) -> Dict:
        """
        Formats the log record for Loki.
        """
        log_data = {
            "timestamp": str(int(time.time() * 1_000_000_000)),
            "level": record.levelname,
            "logger": record.name,
            "message": self.format(record),
        }

        if hasattr(record, "extra"):
            log_data.update(record.extra)

        return log_data

    def send_to_loki(self, log_entry: Dict) -> None:
        """
        Sends the log to Loki via HTTP.
        """
        labels_str = ",".join([f'{k}="{v}"' for k, v in self.labels.items()])
        
        payload = {
            "streams": [
                {
                    "stream": self.labels,
                    "values": [
                        [
                            log_entry["timestamp"],
                            json.dumps({
                                "level": log_entry["level"],
                                "logger": log_entry["logger"],
                                "message": log_entry["message"],
                            })
                        ]
                    ]
                }
            ]
        }

        headers = {"Content-Type": "application/json"}
        
        try:
            response = requests.post(
                self.url,
                json=payload,
                headers=headers,
                timeout=self.timeout
            )
            response.raise_for_status()
        except requests.RequestException:
            pass
