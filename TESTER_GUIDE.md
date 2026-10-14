# Today's Mail Agent — Tester Guide

This test app helps you review recent Gmail messages, sort them into categories, and summarize a message.

## Before you start

- The app owner must add your Google account as an OAuth **test user**. If Google says access is blocked, ask the owner to check this.
- Use your own Google account. The app will never ask you to type your Gmail password into the app.

## Use the app

1. Open the app: <https://today-mail-agent.onrender.com>
2. Sign up or sign in to the app.
3. Choose **Connect Gmail**. Google will ask you to sign in and approve access. Review the permissions before approving.
4. The dashboard loads recent messages automatically. It shows saved messages first, then checks Gmail for updates about every five minutes while the page is open.
5. Choose a category to view matching messages. The app may apply its category labels in Gmail too.
6. Choose **Summarize** on a message to get a summary of that message.

## Automatic sorting

The app checks connected Gmail accounts about every 30 minutes, even when your browser is closed. Emails it can sort get the app's category label in Gmail. Emails it cannot sort stay unlabeled by the app. The dashboard cache is private to your account and keeps only message-list details for up to 24 hours; full message bodies are fetched from Gmail when opened.

This is a test version. Please do not use it for sensitive or important email. To disconnect, use the app's Gmail disconnect option or revoke the app's access in your Google Account security settings.
