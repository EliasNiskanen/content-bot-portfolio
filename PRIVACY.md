# Privacy Policy

Last updated: 18 November 2026

This policy covers **Content Bot**, the software that the Catfish Gaming YouTube channel uses to upload its own
videos. Content Bot is a private tool. It has one user: the channel owner.

## What data the app uses

Content Bot signs in to the channel owner's own YouTube account with Google OAuth. It asks for these scopes:

- `https://www.googleapis.com/auth/youtube.upload`: to upload videos and thumbnails to the owner's channel.
- `https://www.googleapis.com/auth/youtube`: to set video details, manage the channel's playlists and check
  that uploaded videos are processed and public.

The app also reads public data through the YouTube Data API, such as public video titles and view counts, to
pick video topics.

## How the data is stored

- The OAuth token is stored only on the channel owner's own computer, in a file that is never published.
- The app keeps a local SQLite database of the channel's own runs and uploaded video IDs.
- The app does not collect, sell or share personal data about anyone. It does not use Google user data for
  advertising.

## Google API Services User Data Policy

Content Bot's use of information received from Google APIs follows the
[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
including the Limited Use requirements.

## Removing access

You can remove the app's access at any time at
[myaccount.google.com/permissions](https://myaccount.google.com/permissions). Deleting the local token file also
stops all access.

## Contact

Questions about this policy: open an issue at
[github.com/EliasNiskanen/content-bot-portfolio/issues](https://github.com/EliasNiskanen/content-bot-portfolio/issues).
