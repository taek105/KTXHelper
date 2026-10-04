class InvalidStationNameError(Exception):
    pass

class InvalidDateFormatError(Exception):
    pass

class InvalidDateError(Exception):
    pass

class InvalidTimeFormatError(Exception):
    pass


class LoginFailedError(Exception):
    pass


class BrowserWindowClosedError(Exception):
    pass


class RefreshButtonNotFoundError(Exception):
    pass


class KorailAccessBlockedError(Exception):
    pass
