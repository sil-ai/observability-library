#!/usr/bin/env python3
"""
Pydantic schema for standardizing Loki logger labels.
This ensures consistent log structure across all applications.
"""
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator


class LokiLoggerLabels(BaseModel):
    """
    Standard labels for Loki logs.

    These labels are used to organize and filter logs in Grafana.
    All fields are required except for optional metadata.

    Example:
        labels = LokiLoggerLabels(
            project="ecommerce",
            environment="production",
            service="payment-api",
            version="v2.3.1"
        )
    """

    project: str = Field(
        ...,
        min_length=2,
        max_length=50,
        description="Project or application name (e.g., 'ecommerce', 'crm', 'analytics')",
        examples=["aero-api", "aero-django-app", "aqua-api"]
    )

    environment: Literal["release", "main",] = Field(
        ...,
        description="Deployment environment",
        examples=["release", "main"]
    )



    @field_validator("project")
    @classmethod
    def lowercase_alphanumeric(cls, v: str) -> str:
        """Ensure project and service names are lowercase and alphanumeric with hyphens."""
        cleaned = v.lower().strip()
        if not all(c.isalnum() or c in ["-", "_"] for c in cleaned):
            raise ValueError(
                f"'{v}' must contain only alphanumeric characters, hyphens, or underscores"
            )
        return cleaned


    def to_dict(self) -> dict:
        """
        Convert to dictionary, excluding None values.

        Returns:
            dict: Dictionary with only populated fields
        """
        return {k: v for k, v in self.model_dump().items() if v is not None}

    def to_loki_labels(self) -> dict:
        """
        Convert to Loki-compatible label dictionary.
        Alias for to_dict() for clarity.

        Returns:
            dict: Labels ready for LokiHandler
        """
        return self.to_dict()