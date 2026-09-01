# Gmail delegation for reply forwarding

The integration needs domain-wide delegation for only these scopes:

- `https://www.googleapis.com/auth/gmail.readonly`
- `https://www.googleapis.com/auth/gmail.send`

In Google Admin, authorize the service account client ID for those scopes. Store the
service-account JSON as the `GMAIL_SERVICE_ACCOUNT_JSON` secret. Configure every Aether
sending address in `GMAIL_MONITORED_MAILBOXES` and map each Warmy mailbox ID to its
email address in `WARMY_MAILBOX_EMAILS`.

The service impersonates the mailbox that received a Warmy reply, locates the original
message by its RFC 822 Message-ID, and forwards an attached `.eml` copy to
`jw@aetherclean.com`. The database retains message/thread IDs and disposition metadata,
not the reply body. Jordan's mailbox history is read to stop Pipedrive follow-ups after
the prospect replies again.

Test delegation against a dedicated internal test message before enabling provider
writes. Do not grant Drive, Admin, or unrestricted Gmail scopes.
