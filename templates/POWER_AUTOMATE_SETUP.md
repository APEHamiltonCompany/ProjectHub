# Automatic email capture with Power Automate (cloud flow)

Use the **browser** version at https://make.powerautomate.com, not the Power Automate Desktop app.
Desktop's Outlook actions need classic Outlook, which is the part that didn't work.

The flow saves each project email as a `.eml` file in your OneDrive:
`OneDrive - Hamilton Company\Projects\_email_inbox` (the folder already exists, and the tracker already reads it).
OneDrive syncs the file to this PC, and the tracker picks it up on its next run (weekdays at 6:30 AM, or `run_tracker.cmd`).

## Flow 1: Inbox

1. Go to https://make.powerautomate.com and sign in with your Hamilton account.
2. Click **+ Create**, then **Automated cloud flow**.
   - Flow name: `Project emails to tracker`
   - Trigger: search for and pick **When a new email arrives (V3)** (Office 365 Outlook), then click **Create**.
3. Trigger settings:
   - Folder: `Inbox`
   - Include Attachments: `No`
   - Leave the other fields empty.
4. Click **+ New step**, then **Condition**. Change the group to **Or** and add three rows:
   - `Subject` *contains* `SO-`
   - `Subject` *contains* `QTE-`
   - `Subject` *contains* `Project Assignment`
5. Under **If yes**:
   1. Add the action **Export email (V2)** (Office 365 Outlook).
      - Message Id: pick **Message Id** from the dynamic content.
   2. Add the action **Create file** (OneDrive for Business).
      - Folder Path: `/Projects/_email_inbox`
      - File Name: switch to **Expression** and paste:
        `concat(formatDateTime(utcNow(),'yyyyMMdd-HHmmss'),'-',guid(),'.eml')`
      - File Content: pick **Body** from **Export email (V2)** in the dynamic content.
6. Leave **If no** empty, then click **Save**.

## Test it

1. Send yourself an email with the subject `Test SO-000000`.
2. In **My flows > Project emails to tracker > Run history**, check that the run succeeded.
3. Within a minute or two, a `.eml` file appears in `OneDrive - Hamilton Company\Projects\_email_inbox`.
4. Run `run_tracker.cmd`. The test email shows under **UNMATCHED EMAILS**, which is expected because SO-000000 isn't a real project.
5. Delete the test `.eml` file.

## Emails you already have

The flows only catch **new** emails.
For current projects, drag the existing assignment, kickoff and design review emails into `_email_inbox` once (or save them there with File > Save as).

## Notes

- The same email saved twice is fine: the tracker removes duplicates using each email's Message-ID.
- To catch more emails, add rows to the Condition. The tracker ignores any email that doesn't mention an SO, QTE or project number.
- If the flows break (for example, a password change or an expired connection), Power Automate emails you. Fix it under **Data > Connections**.
