import requests as req
from datetime import datetime, timedelta, UTC
from typing import Callable, Optional, Generator, Tuple, Dict, List, Union

def getAllResults(func: Callable, **kwargs) -> Generator[Dict, None, None]:
    '''Generates all results from paginated API endpoint'''
    page: int = 0
    while (results := func(page=page, **kwargs).json()['results']):
        for result in results: 
            yield result
        page+=1

class JAMFAPIHandler:

    '''Handles API requests for the JAMF API.
       Can be used as a context.'''

    def _apiCall(func: Callable) -> Callable:
        '''Decorator that specifies a method as an API call
           Wraps API calls in a method that reauthenticates if required
           (Will reauth to the API if the remaining lifetime of the token is less than 3 minutes)'''
        def wrapper(self: 'JAMFAPIHandler', *args, **kwargs):
            lifetime: Optional[timedelta] = self.checkTokenLifetime()
            if lifetime is None or lifetime < timedelta(minutes=3): self.authenticate()
            response: req.Response = func(self, *args, **kwargs)
            response.raise_for_status()
            return response
        return wrapper

    def __init__(self, url: str, clientID: str, clientSecret: str, timeout: int = 15, **headers):
        self.timeout: int = timeout
        self.headers: Dict = headers
        self.token: Optional[Dict] = None

        self.clientID: str = clientID
        self.clientSecret: str = clientSecret

        self.url: str = url
        self.authenticate()
    
    def authenticate(self) -> None:
        """Authenticate (or re-authenticate) using OAuth client_credentials."""
        # Don’t leak a previous bearer token into the token request
        self.deleteHeaders("Authorization")

        response = req.post(
            f"{self.url}/v1/oauth/token",
            headers={
                "accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                **self.headers,
            },
            data={
                "grant_type": "client_credentials",
                "client_id": self.clientID,
                "client_secret": self.clientSecret,
                "scope": "",
            },
            timeout=self.timeout,
        )

        response.raise_for_status()
        payload = response.json()

        access_token = payload["access_token"]
        expires_in = int(payload.get("expires_in", 0))
        expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

        # Normalize to existing structure expected by checkTokenValid/Lifetime
        self.token = {
            **payload,
            "token": access_token,
            "expires": expires_at.isoformat(),
        }

        self.updateHeaders(Authorization=f"Bearer {access_token}")

    def checkTokenValid(self) -> bool:
        '''Checks if token is valid, returns bool'''
        return self.token is not None and datetime.now(UTC) <= datetime.fromisoformat(self.token['expires'])

    def checkTokenLifetime(self) -> Optional[timedelta]:
        '''Checks how long the token has left to live before invalidation.
           Will return negative time delta if token is expired, or None if
           there is no API token issued'''
        if self.token is None: return None
        return datetime.fromisoformat(self.token['expires']) - datetime.now(UTC)
    
    def invalidate(self) -> None:
        """OAuth tokens: clear locally."""
        if not self.checkTokenValid():
            return
        self.token = None
        self.deleteHeaders("Authorization")
        
    def deleteHeaders(self, *headerKeys: str) -> None:
        '''Deletes HTTP headers with specified keys. I.E.
        >>> api.deleteHeaders('Authorization')
        >>> api.headers['Authorization']
        <Dictionary key error>
        pro tip: do not do this.'''
        
        for key in headerKeys:
            if key in self.headers:
                del self.headers[key]

    def updateHeaders(self, **headers: str) -> None:
        '''Updates HTTP headers with provided kwargs.'''

        self.headers.update(headers)

    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        '''Makes sure to invalidate token upon context exit'''

        for attempt in range(5):
            try:
                self.invalidate()
                return
            except (req.HTTPError, req.Timeout) as e:
                if attempt == 4:
                    raise e
        
    @_apiCall
    def get(self, url: str, timeout: Union[int, Tuple[int, int]] = 15, **kwargs) -> req.Response:
        return req.get(url, headers=self.headers, timeout=timeout, **kwargs)
    
    @_apiCall
    def put(self, url: str, timeout: Union[int, Tuple[int, int]] = 15, **kwargs) -> req.Response:
        return req.put(url, headers=self.headers, timeout=timeout, **kwargs)
    
    @_apiCall
    def post(self, url: str, timeout: Union[int, Tuple[int, int]] = 15, **kwargs) -> req.Response:
        return req.post(url, headers=self.headers, timeout=timeout, **kwargs)

    @_apiCall
    def delete(self, url: str, timeout: Union[int, Tuple[int, int]] = 15, **kwargs) -> req.Response:
        return req.delete(url, headers=self.headers, timeout=timeout, **kwargs)

    def getAuthDetails(self, **kwargs) -> req.Response:
        '''Special case wrapper for JAMFAPIHandler.get. kwargs => kwargs for self.get'''
        return self.get(f'{self.url}/v1/auth', **kwargs)

    def getScripts(self, *, page: int = 0, pageSize: int = 100, sort: List[str] = ['name:asc'], filter: str = '', **kwargs) -> req.Response:
        return self.get(f'{self.url}/v1/scripts',
                    params={
                        'page': page,
                        'page-size': pageSize,
                        'sort': sort,
                        'filter': filter
                    }, **kwargs)
    

    def getScriptByID(self, id: str, **kwargs) -> req.Response:
        return self.get(f'{self.url}/v1/scripts/{id}', **kwargs)


    def postScript(self, data: Dict, **kwargs) -> req.Response:
        return self.post(f'{self.url}/v1/scripts', json=data, **kwargs)
    

    def putScript(self, id: str, data: Dict, **kwargs) -> req.Response:
        return self.put(f'{self.url}/v1/scripts/{id}', json=data, **kwargs)

  
    def deleteScript(self, id: str, **kwargs) -> req.Response:
        return self.delete(f'{self.url}/v1/scripts/{id}', **kwargs)
    

    def downloadScript(self, id: str, **kwargs) -> req.Response:
        #this endpoint HTTP 500s 
        return self.get(f'{self.url}/v1/scripts/{id}/download', **kwargs)
    
  
    def getScriptHistory(self, id: str, **kwargs) -> req.Response:
        return self.get(f'{self.url}/v1/scripts/{id}/history', **kwargs)
    

    def postScriptHistory(self, id: str, data: Dict, **kwargs) -> req.Response:
        return self.post(f'{self.url}/v1/scripts/{id}/history', json=data, **kwargs)


    def getCategories(self, *, page: int = 0, pageSize: int = 100, sort: List[str] = ['id:asc'], filter: str = '', **kwargs) -> req.Response:
        return self.get(f'{self.url}/v1/categories',
                    params={
                        'page': page,
                        'page-size': pageSize,
                        'sort': sort,
                        'filter': filter
                    }, **kwargs)

    # ─────────────────────────────────────────────
    # Computer Extension Attributes
    # ─────────────────────────────────────────────

    def getComputerExtensionAttributes(
        self,
        *,
        page: int = 0,
        pageSize: int = 100,
        sort: List[str] = ["name:asc"],
        filter: str = "",
        **kwargs,
    ) -> req.Response:
        return self.get(
            f"{self.url}/v1/computer-extension-attributes",
            params={
                "page": page,
                "page-size": pageSize,
                "sort": sort,
                "filter": filter,
            },
            **kwargs,
        )

    def getComputerExtensionAttributeByID(self, id: str, **kwargs) -> req.Response:
        return self.get(f"{self.url}/v1/computer-extension-attributes/{id}", **kwargs)

    def postComputerExtensionAttribute(self, data: Dict, **kwargs) -> req.Response:
        return self.post(f"{self.url}/v1/computer-extension-attributes", json=data, **kwargs)

    def putComputerExtensionAttribute(self, id: str, data: Dict, **kwargs) -> req.Response:
        return self.put(f"{self.url}/v1/computer-extension-attributes/{id}", json=data, **kwargs)

    def deleteComputerExtensionAttribute(self, id: str, **kwargs) -> req.Response:
        return self.delete(f"{self.url}/v1/computer-extension-attributes/{id}", **kwargs)
