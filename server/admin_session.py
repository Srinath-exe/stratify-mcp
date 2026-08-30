"""Mint a dashboard session for an existing account, from the shell.

WHY THIS EXISTS. There is signup and there is no login. Signup deliberately refuses an
email that already has an account -- that was an account-takeover path -- so an existing
account whose cookie has expired, or which is being opened on a second machine, has no way
back in. That is fine for API users, who hold a key, and fatal for the admin console,
which is gated on the session cookie.

Until there is a real login (Google, or an emailed magic link), this is the way in, and it
requires shell access to the box, which is the correct bar for the console that shows every
customer's activity.

    python -m server.admin_session you@example.com https://stratify.aeon-labs.site
    python -m server.admin_session you@example.com --rotate   # sign out every browser
"""
import sys

from . import store


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("-")]
    if not args:
        print(__doc__)
        return 2
    email = args[0]
    base = args[1].rstrip("/") if len(args) > 1 else "https://stratify.aeon-labs.site"
    con = store.connect()
    row = con.execute("SELECT account_id, is_admin FROM accounts WHERE email=?",
                      (email,)).fetchone()
    con.close()
    if row is None:
        print(f"No account with email {email!r}.")
        return 1
    if "--rotate" in argv:
        # Clearing the column makes the next session_token() call mint a fresh one, which
        # is the only way to sign every existing browser out of this account.
        con = store.connect()
        with con:
            con.execute("UPDATE accounts SET session_token=NULL WHERE account_id=?",
                        (row["account_id"],))
        con.close()
        print("Previous session invalidated.")
    token = store.session_token(row["account_id"])
    print(f"Session minted for {email} (admin: {bool(row['is_admin'])}).\n")
    print("Set the cookie in the browser's console on the site's own origin, then reload:\n")
    # The cookie the server sets is HttpOnly, so it cannot be written from script -- which
    # is the point of the flag. This one is written by the operator by hand and is
    # therefore not HttpOnly; it is replaced by a proper HttpOnly cookie on the next
    # server-issued session. Keep the lifetime short for that reason.
    print(f'  document.cookie = "stratify_session={token}; '
          f'path=/; max-age=86400; secure; samesite=strict"\n')
    print(f"Then open {base}/admin")
    print("\nThe token is a credential and does not expire. Running this again returns "
          "the SAME token -- it is the account's session, not a new one. To invalidate "
          "every browser signed in as this account, use --rotate.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
