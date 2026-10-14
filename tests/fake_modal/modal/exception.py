"""As excecoes do cliente de verdade que nuvem/ trata."""


class Error(Exception):
    pass


class NotFoundError(Error):
    pass


class AuthError(Error):
    pass
