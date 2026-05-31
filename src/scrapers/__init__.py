from src.scrapers.bdjobs import BDJobsScraper
from src.scrapers.skilljobs import SkillJobsScraper
from src.scrapers.linkedin_email import LinkedInEmailScraper
from src.scrapers.remotive import RemotiveScraper
from src.scrapers.remoteok import RemoteOKScraper
from src.scrapers.weworkremotely import WeWorkRemotelyScraper
from src.scrapers.jobicy import JobicyScraper
from src.scrapers.themuse import TheMuseScraper
from src.scrapers.workingnomads import WorkingNomadsScraper

# shomvob omitted — its job list is behind an obfuscated Supabase RPC (no usable
# public endpoint) and it is a blue-collar-focused platform, so it yields ~0 of
# the software/tech roles this project targets. Revisit if that changes.

ALL_SCRAPERS = [
    # BD portals (local jobs are top priority for a Bangladesh-based candidate)
    BDJobsScraper,
    SkillJobsScraper,
    LinkedInEmailScraper,
    # Global remote-first platforms (English, worldwide)
    RemotiveScraper,
    RemoteOKScraper,
    WeWorkRemotelyScraper,
    JobicyScraper,
    TheMuseScraper,
    WorkingNomadsScraper,
]
