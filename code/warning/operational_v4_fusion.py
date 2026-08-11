"""Version-owned v4 station fusion entry point.

The implementation is shared with the audited operational fusion module, but
the import path is versioned so manifests and reports identify the acceleration
aware contract explicitly.  v1-v3 imports remain untouched.
"""

from warning.operational_v2_fusion import (
    StationEvidenceResult,
    fuse_station_evidence_families_v4,
)

__all__ = ["StationEvidenceResult", "fuse_station_evidence_families_v4"]
