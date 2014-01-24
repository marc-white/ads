# coding: utf-8

""" A Python tool for interacting with NASA's ADS system. """

__author__ = "Andy Casey <andy@astrowizici.st>"

# Standard library
import os
import warnings

# Third party
import requests
import requests_futures.sessions

# Module specific
import parser
from utils import get_dev_key

API_MAX_ROWS = 200
DEV_KEY = get_dev_key()
ADS_HOST = "http://adslabs.org/adsabs/api"

__all__ = ["Article", "search", "metrics", "metadata", "retrieve_article"]

class Article(object):
    """An object to represent a single publication in NASA's Astrophysical
    Data System."""

    aff = ["Unknown"]
    author = ["Anonymous"]
    citation_count = 0
    url = None

    def __init__(self, **kwargs):
        for key, value in kwargs.iteritems():
            setattr(self, key, value)

        if "bibcode" in kwargs:
            self.url = "http://adsabs.harvard.edu/abs/{0}".format(kwargs["bibcode"])

        return None

    def __str__(self):
        return unicode(self).encode("utf-8")

    def __unicode__(self):
        return u"<{0} {1} {2}, {3}>".format(self.author[0].split(",")[0],
            "" if len(self.author) == 1 else (u" & {0}".format(self.author[1].split(",")[0]) if len(self.author) == 2 else "et al."),
            self.year, self.bibcode)    

    def __repr__(self):
        return u"<ads.{0} object at {1}>".format(self.__class__.__name__, hex(id(self)))


    # TODO bibtex @property


    @property
    def references(self):
        """Retrieves reference list for the current article and stores them."""
        if not hasattr(self, '_references'):
            self._references = list(search("references(bibcode:{bibcode})".format(bibcode=self.bibcode), rows="all"))
        return self._references


    @property
    def citations(self):
        """Retrieves citation list for the current article and stores them."""
        if not hasattr(self, '_citations'):
            self._citations = list(search("citations(bibcode:{bibcode})".format(bibcode=self.bibcode), rows="all"))
        return self._citations


    @property
    def metrics(self):
        """Retrieves metrics for the current article and stores them."""

        if not hasattr(self, "_metrics"):
            url = "{0}/record/{1}/metrics/".format(ADS_HOST, self.bibcode)
            payload = {"dev_key": DEV_KEY}

            r = requests.get(url, params=payload)
            if not r.ok: r.raise_for_status()
            self._metrics = r.json()
        return self._metrics


    def build_reference_tree(self, depth):
        """Builds a reference tree for this paper.

        Inputs
        ------
        depth : int
            The number of levels to fetch in the reference tree.

        Returns
        -------
        num_articles_in_tree : int
            The total number of referenced articles in the reference tree.
        """

        try: depth = int(depth)
        except TypeError:
            raise TypeError("depth must be an integer-like type")

        if depth < 1:
            raise ValueError("depth must be a positive integer")

        session = requests_futures.sessions.FuturesSession()

        # To understand recursion, first you must understand recursion.
        level = [self]
        total_articles = len(level) - 1

        for level_num in xrange(depth):

            level_requests = [search("references(bibcode:{bibcode})".format(bibcode=article.bibcode), rows="all") for article in level]

            # Complete all requests
            new_level = []
            for request, article in zip(level_requests, level):
                setattr(article, "references", list(request))
                new_level.extend(article.citations)

            level = sum([new_level], [])
            total_articles += len(level)

        return total_articles          


    def build_citation_tree(self, depth):
        """Builds a citation tree for this paper.

        Inputs
        ------
        depth : int
            The number of levels to fetch in the citation tree.

        Returns
        -------
        num_articles_in_tree : int
            The total number of cited articles in the citation tree.
        """

        try: depth = int(depth)
        except TypeError:
            raise TypeError("depth must be an integer-like type")

        if depth < 1:
            raise ValueError("depth must be a positive integer")

        session = requests_futures.sessions.FuturesSession()

        # To understand recursion, first you must understand recursion.
        level = [self]
        total_articles = len(level) - 1

        for level_num in xrange(depth):

            level_requests = [search("citations(bibcode:{bibcode})".format(bibcode=article.bibcode), rows="all") for article in level]

            # Complete all requests
            new_level = []
            for request, article in zip(level_requests, level):
                setattr(article, "_citations", list(request))
                new_level.extend(article.citations)

            level = sum([new_level], [])
            total_articles += len(level)

        return total_articles     


class APIError(Exception):
    """Exception class for ADS API errors"""
    pass


class search(object):
    """Search ADS and retrieve Article objects."""

    def __init__(self, query=None, authors=None, dates=None, affiliation=None, affiliation_pos=None,
        filter="database:astronomy", acknowledgements=None, fl=None, facet=None, sort="date", 
        order="desc", start=0, rows=20):
        
        arguments = locals().copy()
        del arguments["self"]

        self.payload = _build_payload(**arguments)
        session = requests_futures.sessions.FuturesSession()

        self.active_requests = [session.get(ADS_HOST + "/search/", params=self.payload)]
        self.retrieved_articles = []
        
        # Do we have to perform more queries?
        if rows == "all" or rows > API_MAX_ROWS:
    
            # Get metadata from serial request
            metadata_payload = self.payload.copy()
            metadata_payload["rows"] = 1
            r = requests.get(ADS_HOST + "/search/", params=metadata_payload)
            if not r.ok: r.raise_for_status()
            metadata = r.json()["meta"]

            # Should we issue a warning about excessive rows retrieved?
            if metadata["hits"] >= 10000:
                long_query_message = "ADS query is retrieving more than 10,000 records. Use ads.metadata" \
                    " to find the number of rows for a search query before executing it with ads.search"
                warnings.warn(long_query_message)

            # Are there enough rows such that we actually have to make more requests?
            if API_MAX_ROWS >= metadata["hits"]: return

            if rows == "all":
                num_additional_queries = int(metadata["hits"]/API_MAX_ROWS)
                if not metadata["hits"] % API_MAX_ROWS: num_additional_queries -= 1

            else:
                num_additional_queries = int(rows/API_MAX_ROWS)
                if not rows % API_MAX_ROWS: num_additional_queries -= 1

            # Initiate future requests
            for i in xrange(1, num_additional_queries + 1):
                # Update payload to start at new point
                self.payload["start"] = i * API_MAX_ROWS

                # Limit total number of rows if required
                if rows != "all" and (i + 1) * API_MAX_ROWS > rows:
                    self.payload["rows"] = rows - i * API_MAX_ROWS

                self.active_requests.append(session.get(ADS_HOST + "/search/", params=self.payload))

    def __iter__(self):
        return self

    def next(self):

        if len(self.active_requests) == 0 and len(self.retrieved_articles) == 0:
            raise StopIteration

        if len(self.retrieved_articles) == 0:
            active_request = self.active_requests.pop(0)
            response = active_request.result().json()

            if "error" in response:
                raise APIError(response["error"])

            self.retrieved_articles.extend([Article(**article_info) for article_info in response["results"]["docs"]])
            
        if len(self.retrieved_articles) == 0:
            raise StopIteration

        return self.retrieved_articles.pop(0)


def metrics(author, metadata=False):
    """ Retrieves metrics for a given author query """

    payload = {
        "q": author,
        "dev_key": DEV_KEY,
    }
    r = requests.get(ADS_HOST + "/search/metrics/", params=payload)
    if not r.ok: r.raise_for_status()
    
    contents = r.json()
    if "error" in contents:
        raise APIError(contents["error"])
    metadata, results = contents["meta"], contents["results"]

    if metadata:
        return (results, metadata)
    return results


def metadata(query=None, authors=None, dates=None, affiliation=None, affiliation_pos=None,
    filter="database:astronomy"):
    """Search ADS for the given inputs and just return the metadata."""

    payload = _build_payload(**locals())
    payload["rows"] = 1 # It's meta-data, baby.
    r = requests.get(ADS_HOST + "/search/", params=payload)
    if not r.ok: r.raise_for_status()
    
    contents = r.json()
    if "error" in contents:
        raise APIError(contents["error"])
    return contents["meta"]


def _build_payload(query=None, authors=None, dates=None, affiliation=None, affiliation_pos=None,
    filter=None, fl=None, acknowledgements=None, facet=None, sort="date", order="desc", start=0,
    rows=20):
    """Builds a dictionary payload for NASA's ADS based on the input criteria."""

    query = parser.query(query, authors, dates)

    # Check inputs
    start, rows = parser.rows(start, rows, max_rows=API_MAX_ROWS)
    sort, order = parser.ordering(sort, order)

    # Filters
    pubdate_filter = parser.dates(dates)
    affiliation_filter = parser.affiliation(affiliation, affiliation_pos)
    acknowledgements_filter = parser.acknowledgements(acknowledgements)

    filters = (pubdate_filter, affiliation_filter, acknowledgements_filter)
    for query_filter in filters:
        if query_filter is not None:
            query += query_filter

    payload = {
        "q": query,
        "dev_key": DEV_KEY,
        "sort": "{sort} {order}".format(sort=sort.upper(), order=order),
        "start": start,
        "fmt": "json",
        "rows": rows,
    }
    additional_payload = {
        "fl": fl,
        "filter": filter,
        "facet": facet
    }
    payload.update(additional_payload)
        
    return payload


def retrieve_article(article, output_filename, clobber=False):
    """Download the journal article (preferred) or pre-print version
    of the article provided, and save the PDF to disk.

    Inputs
    ------
    article : `Article` object
        The article to retrieve.

    output_filename : str
        The filename to save the article to.

    clobber : bool, optional
        Overwrite the filename if it already exists.
    """

    if os.path.exists(output_filename) and not clobber:
        raise IOError("output filename (\"{filename}\") exists and we've been "
            "asked not to clobber it.".format(filename=output_filename))

    # Get the ADS url
    ads_redirect_url = "http://adsabs.harvard.edu/cgi-bin/nph-data_query"
    arxiv_payload = {
        "bibcode": article.bibcode,
        "link_type": "PREPRINT",
        "db_key": "PRE"
    }
    article_payload = {
        "bibcode": article.bibcode,
        "link_type": "ARTICLE",
        "db_key": "AST"
    }
    
    # Let's try and download the article from the journal first
    article_r = requests.get(ads_redirect_url, params=article_payload)

    if not article_r.ok or "Requested scanned pages are not available" in article_r.text:

        # Use the arxiv payload
        arxiv_r = requests.get(ads_redirect_url, params=arxiv_payload)

        if not arxiv_r.ok:
            return False

        article_pdf_url = arxiv_r.url.replace("abs", "pdf")

    else:
        # Parser the PDF url
        article_pdf_url = None
    
    article_pdf_r = requests.get(article_pdf_url)
    if not article_pdf_r.ok: article_pdf_r.raise_for_status()

    with open(output_filename, "wb") as fp:
        fp.write(article_pdf_r.content)

    return True
