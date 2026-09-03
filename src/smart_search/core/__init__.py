"""The dependency-light v1 domain core."""
from .models import Candidate, Evidence, FusedCandidate, RankedCandidate, ResearchRun, RetrievalPolicy
from .ranking import DEFAULT_RRF_K, canonicalize_url, deduplicate_candidates, reciprocal_rank_fusion

__all__ = [
    "Candidate", "Evidence", "FusedCandidate", "RankedCandidate", "ResearchRun", "RetrievalPolicy",
    "DEFAULT_RRF_K", "canonicalize_url", "deduplicate_candidates", "reciprocal_rank_fusion",
]
