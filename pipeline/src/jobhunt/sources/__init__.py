"""Job sources: one module per site, configured in config/sources.yaml."""

from jobhunt.config.models import (
    BdjobsSource,
    DisabledSource,
    HimalayasSource,
    JobicySource,
    RemoteokSource,
    RemotiveSource,
    RssSource,
    SourceSettings,
    WorkingNomadsSource,
)
from jobhunt.sources.base import FetchContext, Source
from jobhunt.sources.bdjobs import Bdjobs
from jobhunt.sources.himalayas import Himalayas
from jobhunt.sources.models import RawListing, SourceResult
from jobhunt.sources.remote_boards import Jobicy, RemoteOk, Remotive, WorkingNomads
from jobhunt.sources.rss import RssFeeds


def build_source(name: str, settings: SourceSettings) -> Source | None:
    """The implementation for a configured source; None for `kind: disabled`."""
    match settings:
        case BdjobsSource():
            return Bdjobs(name, settings)
        case RemoteokSource():
            return RemoteOk(name, settings)
        case JobicySource():
            return Jobicy(name, settings)
        case WorkingNomadsSource():
            return WorkingNomads(name, settings)
        case RemotiveSource():
            return Remotive(name, settings)
        case HimalayasSource():
            return Himalayas(name, settings)
        case RssSource():
            return RssFeeds(name, settings)
        case DisabledSource():
            return None


__all__ = ["FetchContext", "RawListing", "Source", "SourceResult", "build_source"]
