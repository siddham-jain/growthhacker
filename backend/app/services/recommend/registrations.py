# importing this module registers 07's built-in recommenders and quality gates
from app.services.recommend.gates import structural, text
from app.services.recommend.recommenders import creator, patterns, video_idea

__all__ = ["creator", "patterns", "structural", "text", "video_idea"]
