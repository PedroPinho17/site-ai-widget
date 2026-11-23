"""site-ai-widget: embeddable AI chat widget for company websites."""

__version__ = "0.1.0"


def main() -> None:
    """CLI entry: run the API on 127.0.0.1 (local dev)."""
    import uvicorn

    from site_ai_widget.api.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "site_ai_widget.api.main:app",
        host="127.0.0.1",
        port=settings.app_port,
        reload=False,
    )
