# Resend SMTP setup

PocketPerks sends password-reset, login-email confirmation, team invitation, and role-change confirmation messages through SMTP. Resend works with this existing mailer; no Resend SDK or hosted Supabase account is required.

## Configure Resend

1. Create a Resend account and add `pocket-perks.co.uk` as a sending domain.
2. Ask the domain DNS administrator to add the exact SPF/DKIM records Resend displays. Wait until Resend marks the domain verified.
3. Create a Resend API key. Treat it as a password and do not paste it into chat or commit it to Git.
4. In the VPS project root `.env`, set `APP_ORIGIN=https://pocket-perks.co.uk`.
5. In `backend/.env`, set:

   ```dotenv
   SMTP_HOST=smtp.resend.com
   SMTP_PORT=587
   SMTP_USERNAME=resend
   SMTP_PASSWORD=<Resend API key>
   SMTP_FROM_EMAIL=auth@pocket-perks.co.uk
   SMTP_FROM_NAME=PocketPerks
   SMTP_USE_TLS=true
   ```

   Use `auth@pocket-perks.co.uk` only after verifying that domain in Resend. Keep both environment files private; on the VPS they should be readable only by the server administrator.

6. Recreate the backend container so it reads the new environment values.

## What the email flows do

- Forgot password sends a short-lived reset link. The password changes only after the user opens it and submits a new password.
- Changing a login email sends confirmation links to both the current and new addresses. Both must be confirmed before the address changes.
- A new team member receives an invitation link, chooses their own password, and is added only after accepting. Invitations expire after 48 hours.
- A role change is applied after the user confirms it by email; until then their current role remains. Removing Admin access takes effect immediately for security, sends a notice email, and revokes active sessions.
- Remove user disables access and revokes sessions while preserving historical campaign and review records. It does not erase audit history.

## Verify delivery

1. Request a password-reset link for an account you control. The request itself does not change its password; the password changes only after the link is opened and a new password is submitted.
2. In Account Settings, request a login-email change. The current implementation sends confirmation links to both the existing and new email addresses. Both links must be confirmed before the login address changes.
3. In Account Settings, invite a test user and confirm the invite link. Then request a role change and confirm that link. Check Resend's email activity and backend logs if a message does not arrive. Never display or share the SMTP password/API key while troubleshooting.

Resend's current Free plan lists 3,000 emails per month and a 100-email daily limit. This is suitable for low-volume account emails, not PocketPerks' outreach campaign sending. Provider limits can change.
