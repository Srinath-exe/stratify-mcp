"""Sign in with Google.

WHY GOOGLE AND NOT A PASSWORD. A password means a reset flow, an email sender, a hashing
policy, a breach story and a support burden -- five things to get right in order to learn
one fact we can be told authoritatively for free. There is no password anywhere in this
service and there never needs to be.

THE FLOW, and why each piece is there:

    /auth/google          mints `state` + PKCE `code_verifier`, stows them in a short-lived
                          signed cookie, redirects to Google.
    /auth/google/callback Google sends back `code` + `state`. We compare state against the
                          cookie (CSRF: without it, an attacker can complete a login IN
                          YOUR BROWSER against THEIR account and quietly collect whatever
                          you do next). We then exchange the code, server to server, over
                          TLS, presenting the client secret and the verifier.

WHY THE ID TOKEN IS TRUSTED WITHOUT FETCHING GOOGLE'S SIGNING KEYS. It does not arrive
through the browser. It comes back on the direct HTTPS response to a request THIS server
made to accounts.google.com, authenticated with the client secret -- the confidential-client
path. A forged token would require breaking TLS to Google. The claims are still checked
(issuer, audience, expiry) because a correct token used in the wrong place is a real bug
even when nobody is attacking, and `email_verified` is checked because Google will hand
out an unverified address on some Workspace configurations and an unverified address is
not an identity.

DEGRADES HONESTLY. With no credentials configured, `enabled()` is False, the button is not
drawn, and the routes say so instead of 500ing. That matters because it is the state the
service is in until someone pastes a client id into the environment.
"""
import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SCOPES = "openid email profile"

# The handshake cookie. Separate from the session cookie and deliberately short-lived: it
# is a half-finished login, not a login.
FLOW_COOKIE = "stratify_oauth"
FLOW_TTL = 600          # ten minutes to click through Google and come back
CALLBACK_PATH = "/auth/google/callback"

# --------------------------------------------------------------------- One Tap
#
# The small "Continue as ..." prompt. It is the same identity, arriving by a DIFFERENT and
# strictly weaker path, and the difference is the whole of the security story here.
#
# The code flow above is trusted because the token comes back on a direct TLS response to a
# request THIS server made, authenticated with the client secret. One Tap's token arrives
# THROUGH THE BROWSER, from a script, on a form POST. Anybody can post anything to that
# endpoint. So the token has to be VERIFIED rather than merely parsed -- treating a One Tap
# credential the way we treat the code-exchange response would be an authentication bypass:
# forge a JWT with someone's email in it and you are them.
#
# Verification is done by asking Google, at `tokeninfo`, over a server-to-server TLS call --
# the same trust basis as the code exchange. The alternative is fetching Google's JWKS and
# checking an RS256 signature locally, which means either a new dependency in an
# internet-facing image or hand-rolled RSA. For a path that runs once per sign-in, one
# outbound call is the cheaper risk by a wide margin.
ONETAP_PATH = "/auth/google/onetap"
TOKENINFO_ENDPOINT = "https://oauth2.googleapis.com/tokeninfo"
# Google sets this cookie AND posts the same value in the form body. Comparing them is the
# double-submit check that stops a third-party page posting a credential of its own here.
CSRF_FIELD = "g_csrf_token"
GSI_SCRIPT = "https://accounts.google.com/gsi/client"


class OAuthError(Exception):
    """Sign-in failed for a reason worth showing a person."""


def client_id():
    return os.getenv("GOOGLE_CLIENT_ID", "").strip()


def client_secret():
    return os.getenv("GOOGLE_CLIENT_SECRET", "").strip()


def enabled():
    return bool(client_id() and client_secret())


def redirect_uri(base_url):
    return base_url.rstrip("/") + CALLBACK_PATH


# ------------------------------------------------------------------ the handshake

def begin(base_url, next_path="/app"):
    """-> (google_url, flow_cookie_value). Nothing is stored server-side.

    The state and the PKCE verifier live in the cookie rather than in a table, so a login
    started on a machine that never comes back leaves nothing to expire. The cookie is
    signed with the same secret that pepper-hashes keys, so a caller cannot mint their own
    state and skip the CSRF check.
    """
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    payload = {"state": state, "verifier": verifier, "next": _safe_next(next_path),
               "exp": time.time() + FLOW_TTL}
    query = urllib.parse.urlencode({
        "client_id": client_id(),
        "redirect_uri": redirect_uri(base_url),
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # Ask every time rather than silently reusing a Google session: on a shared
        # machine the silent path signs in whoever used the browser last.
        "prompt": "select_account",
    })
    return f"{AUTH_ENDPOINT}?{query}", _sign(payload)


def finish(base_url, code, state, flow_cookie):
    """-> {sub, email, name, picture, next}. Raises OAuthError with a readable reason."""
    if not enabled():
        raise OAuthError("Google sign-in is not configured on this server.")
    flow = _unsign(flow_cookie)
    if flow is None:
        raise OAuthError("That sign-in link has expired. Please try again.")
    if not code:
        raise OAuthError("Google did not return an authorisation code.")
    # compare_digest, not ==: state is a secret and a timing-visible comparison leaks it
    # one character at a time.
    if not state or not secrets.compare_digest(str(state), flow["state"]):
        raise OAuthError("That sign-in did not start here. Please try again.")
    token = _exchange(base_url, code, flow["verifier"])
    claims = _claims(token.get("id_token"))
    if claims.get("iss") not in ISSUERS:
        raise OAuthError("The sign-in token did not come from Google.")
    if claims.get("aud") != client_id():
        raise OAuthError("The sign-in token was issued for a different application.")
    if float(claims.get("exp", 0)) < time.time():
        raise OAuthError("The sign-in token had already expired.")
    if not claims.get("email"):
        raise OAuthError("Google did not share an email address with us.")
    # A Workspace admin can hand out an address the user has never proved they hold.
    # Accepting it would let one tenant's member claim another's account by email.
    if claims.get("email_verified") not in (True, "true"):
        raise OAuthError("That Google account's email address is not verified.")
    return {"sub": claims["sub"], "email": claims["email"].lower(),
            "name": claims.get("name"), "picture": claims.get("picture"),
            "next": flow.get("next") or "/app"}


def _exchange(base_url, code, verifier):
    """The one outbound call this server makes. urllib, not httpx, DELIBERATELY.

    httpx is present in a development interpreter because FastAPI's test client pulls it
    in, and absent from the slim runtime image -- which is exactly how this shipped once
    and crash-looped on import. One form POST does not justify a dependency in an
    internet-facing image, and the standard library verifies certificates by default.
    """
    body = urllib.parse.urlencode({
        "code": code, "client_id": client_id(), "client_secret": client_secret(),
        "redirect_uri": redirect_uri(base_url), "grant_type": "authorization_code",
        "code_verifier": verifier}).encode()
    request = urllib.request.Request(
        TOKEN_ENDPOINT, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # Google's own description is the useful half. The raw body is never shown to a
        # person, because it can carry the authorisation code back out onto the page.
        detail = ""
        try:
            payload = json.loads(exc.read())
            detail = payload.get("error_description") or payload.get("error") or ""
        except (ValueError, OSError):
            pass
        raise OAuthError(
            f"Google rejected the sign-in{': ' + detail if detail else '.'}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise OAuthError("Could not reach Google to complete sign-in.") from exc


def _claims(id_token):
    if not id_token or id_token.count(".") != 2:
        raise OAuthError("Google's response did not contain an identity token.")
    body = id_token.split(".")[1]
    body += "=" * (-len(body) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(body))
    except (ValueError, TypeError) as exc:
        raise OAuthError("Google's identity token could not be read.") from exc


# ------------------------------------------------------------- the signed cookie

def _secret():
    # The same secret the key store peppers with. One secret to deploy, not two -- and if
    # it is absent the process should not come up pretending to have security.
    from . import store
    return store.PEPPER


def _sign(payload):
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    mac = hashlib.blake2b(body.encode(), key=_secret(), digest_size=16).hexdigest()
    return f"{body}.{mac}"


def _unsign(cookie):
    if not cookie or "." not in cookie:
        return None
    body, _, mac = cookie.rpartition(".")
    want = hashlib.blake2b(body.encode(), key=_secret(), digest_size=16).hexdigest()
    if not secrets.compare_digest(mac, want):
        return None
    body += "=" * (-len(body) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(body))
    except (ValueError, TypeError):
        return None
    if float(payload.get("exp", 0)) < time.time():
        return None
    return payload


def _safe_next(path):
    """Only ever a path on this site. An open redirect here would let a phishing page send
    somebody through a real Google login and land them somewhere else entirely."""
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        return "/app"
    return path[:200]


def verify_id_token(credential, csrf_cookie, csrf_field):
    """A One Tap credential -> the same {sub, email, name, picture} dict `finish` returns.

    Raises OAuthError on anything that does not check out. Every branch below is a way for
    this endpoint to be abused, not a way for a legitimate sign-in to be unusual.
    """
    if not enabled():
        raise OAuthError("Google sign-in is not configured on this server.")
    # DOUBLE SUBMIT, AND NOT OPTIONAL. Google sets g_csrf_token as a cookie and posts the
    # same value; a page on another origin can post a body but can neither read nor set
    # our cookie, so a mismatch means the post did not come from Google's prompt here.
    #
    # This was first written to run the comparison only when one of the two was present,
    # which is a check an attacker turns off by sending NEITHER -- the guard was there and
    # did nothing, which is worse than not having written it, because it reads as
    # protection. Both values are now required, always.
    if not csrf_cookie or not csrf_field or \
            not secrets.compare_digest(str(csrf_cookie), str(csrf_field)):
        raise OAuthError("That sign-in did not start here. Please try again.")
    if not credential or credential.count(".") != 2:
        raise OAuthError("Google did not send a usable identity token.")
    claims = _tokeninfo(credential)
    # tokeninfo has already checked the signature and the expiry. These are checked AGAIN
    # because a token that is valid for a DIFFERENT application is still a valid token,
    # and accepting one would let any Google developer sign in as anybody here.
    if claims.get("iss") not in ISSUERS:
        raise OAuthError("The sign-in token did not come from Google.")
    if claims.get("aud") != client_id():
        raise OAuthError("The sign-in token was issued for a different application.")
    if float(claims.get("exp", 0)) < time.time():
        raise OAuthError("The sign-in token had already expired.")
    if not claims.get("email"):
        raise OAuthError("Google did not share an email address with us.")
    if claims.get("email_verified") not in (True, "true"):
        raise OAuthError("That Google account's email address is not verified.")
    return {"sub": claims["sub"], "email": claims["email"].lower(),
            "name": claims.get("name"), "picture": claims.get("picture")}


def _tokeninfo(credential):
    url = f"{TOKENINFO_ENDPOINT}?{urllib.parse.urlencode({'id_token': credential})}"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # A 400 here is the normal answer for a forged, expired or malformed token. It is
        # not an error condition of ours and must not read like one.
        raise OAuthError("Google could not validate that sign-in. Please try again.") \
            from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise OAuthError("Could not reach Google to complete sign-in.") from exc
