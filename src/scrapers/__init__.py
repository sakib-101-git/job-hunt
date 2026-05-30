from src.scrapers.bdjobs import BDJobsScraper
from src.scrapers.linkedin_email import LinkedInEmailScraper
from src.scrapers.remotive import RemotiveScraper
from src.scrapers.remoteok import RemoteOKScraper
from src.scrapers.weworkremotely import WeWorkRemotelyScraper
from src.scrapers.jobicy import JobicyScraper
from src.scrapers.themuse import TheMuseScraper
from src.scrapers.workingnomads import WorkingNomadsScraper

# shomvob and skilljobs removed — both return 404 (dead URLs) and are BD-office-only anyway

ALL_SCRAPERS = [
    # BD portal — kept in case any remote/WFH listings appear from Bangladesh
    BDJobsScraper,
    LinkedInEmailScraper,
    # Global remote-first platforms (English, worldwide)
    RemotiveScraper,
    RemoteOKScraper,
    WeWorkRemotelyScraper,
    JobicyScraper,
    TheMuseScraper,
    WorkingNomadsScraper,
]
