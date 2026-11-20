# importing the detector modules registers them in trigger_registry
from app.services.refresh.triggers import broken_link, hygiene, keywords, news_entity

__all__ = ["broken_link", "hygiene", "keywords", "news_entity"]
