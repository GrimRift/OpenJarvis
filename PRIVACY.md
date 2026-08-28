# Privacy Policy

**Last updated: 2026-08-28**

Sage is a personal, self-hosted AI assistant. It is installed and run by an
individual on their own computer. There is no hosted service, no operator
account, and no server that receives your data. This policy describes what the
software does with data on the machine it is installed on.

## Who this applies to

The person who installs and runs Sage. Sage is a single-user application; the
only Google Account it can access is the one whose owner personally completes
the sign-in flow on their own machine.

## What Google data Sage accesses

With your explicit consent at sign-in, Sage may request:

| Scope | What it is used for |
|---|---|
| `gmail.modify` | Reading your mail so you can search and ask questions about it, and applying labels or archiving messages **only** when you approve that specific action |
| `calendar` | Reading and creating calendar events and reminders |
| `drive.readonly` | Reading Drive documents so they can be searched |
| `contacts.readonly` | Resolving names to contacts |
| `tasks.readonly` | Reading your task lists |
| `openid`, `email`, `profile` | Identifying which account is connected |

You can grant a subset; Sage degrades to the features the granted scopes allow.

## Where your data is stored

On your own computer, and nowhere else by default:

- Indexed content from the services above is stored in a local database file so
  it can be searched offline.
- OAuth tokens are stored in a local file in your application data directory.
- Conversation history, scheduled tasks, and logs are stored locally.

Nothing is uploaded to the developer. There is no analytics, telemetry, or
crash reporting that leaves your machine.

## When data leaves your machine

Sage answers questions using a language model. Which model is used is your
configuration choice:

- **A local model** (for example, a model run by Ollama on your own machine):
  your data does not leave your computer.
- **A cloud model provider** that you configure with your own API key: the
  relevant excerpts needed to answer your request — which may include email,
  calendar, or document content — are sent to that provider under their terms
  and privacy policy. Sage does not send data to any provider you have not
  configured.

If you connect an optional messaging channel (for example, Telegram) to receive
notifications, the notification content you have configured is delivered
through that service.

## Use of Google user data

Google user data is used solely to provide the assistant features described
above, at your direction. It is **not** sold, **not** shared with third parties
beyond the model provider you configure, **not** used for advertising, and
**not** used to train any model.

## Retention and deletion

Data is retained locally until you delete it. To remove everything:

- Revoke Sage's access at
  [myaccount.google.com/permissions](https://myaccount.google.com/permissions).
- Delete the application data directory on your machine, which removes the
  stored tokens and the local index.

## Security

Data is protected by the security of your own computer and operating system
account. Credentials are stored outside the source repository. You are
responsible for the security of the machine Sage runs on and of any API keys
you configure.

## Changes

Changes to this policy will be committed to this file, and its history is
visible in this repository.

## Contact

Questions about this policy: yansonmark18@gmail.com
