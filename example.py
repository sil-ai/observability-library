"""
Example usage of observability_library
"""
import logging
from observability_library import LokiHandler


def main():
    logger = logging.getLogger("example-app")
    logger.setLevel(logging.INFO)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)

    loki_handler = LokiHandler(
        url="http://localhost:3100/loki/api/v1/push",
        labels={
            "app": "example-application",
            "env": "development",
            "service": "backend"
        },
        timeout=5
    )
    logger.addHandler(loki_handler)

    logger.info("Application started successfully")
    logger.warning("This is a warning message")
    logger.error("This is an error message")
    
    try:
        result = 10 / 0
    except ZeroDivisionError:
        logger.exception("Error dividing by zero")


if __name__ == "__main__":
    main()
