"""Grant or revoke admin on an account.

A CLI rather than a route: the first admin has to be created by someone with shell access
to the box, because any in-app path to creating the first admin is a privilege-escalation
route by definition.

    python -m server.grant_admin sahej@example.com
    python -m server.grant_admin sahej@example.com --revoke
"""
import sys

from . import store


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("-")]
    if len(args) != 1:
        print(__doc__)
        return 2
    email, revoke = args[0], "--revoke" in argv
    changed = store.grant_admin(email, on=not revoke)
    if not changed:
        print(f"No account with email {email!r}. Sign up first, then run this.")
        return 1
    print(f"{'Revoked admin on' if revoke else 'Granted admin to'} {email}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
