# Ruby Grind Bot

A multi-functional Discord bot designed for communities centered around the game "THE FINALS". It offers features for player stats tracking, server moderation, community engagement, and more.

## Features

*   **THE FINALS Integration:**
    *   Player stats lookup (`/gamestats`, `/lookup`).
    *   Rank checking and comparison (`/rank`, `/rank_compare`, `/ruby`, `/check_ruby`).
    *   Leaderboard tracking.
    *   Automatic rank role assignment based on in-game rank (`/task_rank_roles`).
    *   Map rotation tracking and prediction embed (`/maprotation`, `/reportmap`).
    *   Account linking between Discord and THE FINALS (`/link_setup`, `/force_link`, `/delete_link`, `/checklink`).
*   **Moderation:**
    *   Timed bans with appeal system (`/ban`, `/unban`).
    *   Warning system with tracking, clearing, and disagreement tickets (`/warn`, `/warnings`, `/clearwarning`).
    *   Message management: pruning, channel locking, slowmode (`/prune`, `/lock`, `/unlock`, `/slowmode`).
    *   Moderator activity point tracking (`/modleaderboard`, `/checkmod`, `/givepoints`).
    *   Restrict/unrestrict user actions (`/restrict`, `/unrestrict`).
*   **Server Management:**
    *   Configurable feature management via `/setup`.
    *   Welcome channel messages (`/setwelcomechannel`, `/testwelcome`).
    *   Auto-role assignment via reactions/buttons (`/autoassignroles`).
    *   Professional announcement formatting (`/setannouncementchannel`, `/optout_dms`, `/optin_dms`).
    *   Server logging (integrated into various modules).
*   **Community & Engagement:**
    *   Support ticket system with categories, transcripts, and feedback (`/ticket`, `/transcripts`).
    *   Twitch "Go Live" notifications and role assignment (`/linktwitch`, `/unlinktwitch`, `/twitchnotificationchannel`).
    *   Giveaway system (`/giveaway`, `/reroll`, `/endgiveaway`).
    *   XP and Leveling system based on messages and voice activity (`/level`, `/leaderboard`, `/give_exp`).
    *   Casino/Gambling games (Blackjack, Crash).
*   **Utility:**
    *   Direct messaging system for staff with read receipts (`/message`, `/setmessage`).
    *   Anti-bot verification for new accounts.

## Setup

1.  **Prerequisites:**
    *   Python 3.10+
    *   Git
    *   Azure Cosmos DB account
    *   Twitch Developer Application credentials

2.  **Clone Repository:**
    ```bash
    git clone https://github.com/xJakubi/Rubygrindbot.git
    cd Rubygrindbot
    ```

3.  **Create `.env` File:**
    Create a file named `.env` in the root directory and add the following environment variables with your credentials:
    ```env
    TOKEN=YOUR_DISCORD_BOT_TOKEN
    COSMOS_ENDPOINT=YOUR_COSMOS_DB_ENDPOINT
    COSMOS_KEY=YOUR_COSMOS_DB_PRIMARY_KEY
    COSMOS_DATABASE=YOUR_COSMOS_DB_DATABASE_NAME
    TWITCH_CLIENT_ID=YOUR_TWITCH_CLIENT_ID
    TWITCH_CLIENT_SECRET=YOUR_TWITCH_CLIENT_SECRET
    # Optional: Define specific Admin User IDs for certain features
    # ADMIN_USER_ID_1=YOUR_DISCORD_USER_ID
    # ADMIN_USER_ID_2=ANOTHER_DISCORD_USER_ID
    ```

4.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

5.  **Run the Bot:**
    ```bash
    python bot_main.py
    ```

6.  **Initial Bot Setup (in Discord):**
    *   When the bot joins a server, it should post a setup message prompting administrators to configure features.
    *   If the message doesn't appear, or you need to reconfigure, an administrator can run the `/setup` command.
    *   Use the dropdown menu in the setup message to enable or disable features for the server. Click "Save Setup" when done.

## Configuration

*   **Feature Management:** The primary way to configure the bot is using the `/setup` command within Discord. This allows administrators to enable or disable specific modules (like the Ticket System, XP System, etc.) for their server. Settings are stored in the configured Azure Cosmos DB.
*   **Configuration Files:** While most settings are managed via `/setup` and stored in Cosmos DB, the bot may also use local JSON files for some data persistence or legacy configurations. Example files (`*.json.example`) are provided in the repository (e.g., `guild_settings.json.example`, `ticket_config.json.example`, `twitch_settings.json.example`). These show the expected data structure but are generally managed by the bot itself or specific setup commands.

## Commands (Overview)

The bot uses Discord's slash commands. Here's a summary of key features and their commands:

---

**Core**

*   `/setup`
    *   **(Admin)** Opens the feature management panel to enable/disable bot modules for the current server.

---

**THE FINALS Integration**

*   `/gamestats`
    *   View *your* linked THE FINALS leaderboard statistics. Requires account linking first.
*   `/lookup`
    *   Look up THE FINALS stats for *any* player by their in-game name.
    *   *Usage:* `/lookup in_game_name:<Name#0000>`
*   `/rank`
    *   Displays a user's current THE FINALS rank (requires account linking).
    *   *Usage:* `/rank [user:<@User>]`
*   `/rank_compare`
    *   Compares the ranks of two players (requires account linking for both).
    *   *Usage:* `/rank_compare user1:<@User> user2:<@User>`
*   `/ruby`
    *   Check *your* progress towards Ruby rank (top 500) in THE FINALS. Requires account linking.
*   `/check_ruby`
    *   Check if *another* player has achieved Ruby rank. Requires account linking for the target user.
    *   *Usage:* `/check_ruby user:<@User>`
*   `/task_rank_roles`
    *   **(Admin)** Manages the automatic assignment of roles based on player ranks fetched from THE FINALS API. Includes subcommands like `update_now`.
*   `/maprotation`
    *   Displays the current predicted map rotation embed. Use `/maprotation setup` (Admin) to configure the embed channel/message.
*   `/reportmap`
    *   Report the current map you are playing to help improve rotation predictions.
    *   *Usage:* `/reportmap map:<MapName>`
*   `/link_setup`
    *   **(Admin)** Sets up the channel for the account verification/linking embed.
    *   Users interact with buttons (\"Verify\", \"Delete Link\") in the embed posted by this command to link/unlink their Discord account to their THE FINALS in-game name.
*   `/force_link`
    *   **(Staff)** Manually links a Discord user to a THE FINALS in-game name.
    *   *Usage:* `/force_link user:<@User> in_game_name:<Name#0000>`
*   `/checklink`
    *   Checks if you or another user has linked their THE FINALS account.
    *   *Usage:* `/checklink [user:<@User>]`
*   `/delete_link`
    *   **(User Command via Button)** Allows a user to unlink their *own* account via the verification embed message.

---

**Moderation**

*   `/ban`
    *   **(Moderator/Admin)** Bans a user for a specified duration with a reason. Includes an appeal process.
    *   *Usage:* `/ban user:<@User> duration:<dd:hh:mm> reason:<Text>`
*   `/unban`
    *   **(Moderator/Admin)** Unbans a user.
    *   *Usage:* `/unban user_id:<DiscordUserID>`
*   `/warn`
    *   **(Moderator/Admin)** Issues a warning to a user.
    *   *Usage:* `/warn user:<@User> reason:<Text>`
*   `/warnings`
    *   **(Moderator/Admin)** View the warnings for a specific user.
    *   *Usage:* `/warnings user:<@User>`
*   `/clearwarning`
    *   **(Admin)** Clears a specific warning by its ID.
    *   *Usage:* `/clearwarning user:<@User> warning_id:<WarningID>`
*   `/prune`
    *   **(Admin - Manage Messages)** Deletes a specified number of messages.
    *   *Usage:* `/prune amount:<Number>`
*   `/lock` / `/unlock`
    *   **(Admin - Manage Channels)** Locks or unlocks the current channel, preventing non-admins from sending messages.
*   `/slowmode`
    *   **(Admin - Manage Channels)** Sets slowmode in the current channel.
    *   *Usage:* `/slowmode seconds:<Number>` (Use 0 to disable)
*   `/restrict`
    *   **(Staff)** Restricts a user from performing certain actions (e.g., using specific commands, reacting). Configuration depends on implementation.
    *   *Usage:* `/restrict user:<@User> reason:<Text> [duration:<dd:hh:mm>]`
*   `/unrestrict`
    *   **(Staff)** Removes restrictions previously applied with `/restrict`.
    *   *Usage:* `/unrestrict user:<@User>`
*   `/modleaderboard`
    *   **(Admin)** Shows the moderator activity points leaderboard.
*   `/checkmod`
    *   **(Admin)** Checks the activity point report for a specific moderator.
    *   *Usage:* `/checkmod moderator:<@Moderator>`
*   `/givepoints`
    *   **(Admin)** Manually awards points to a moderator.
    *   *Usage:* `/givepoints moderator:<@Moderator> points:<Number> reason:<Text>`

---

**Server Management**

*   `/setwelcomechannel`
    *   **(Admin)** Sets the current channel to send welcome messages and images when new members join.
*   `/testwelcome`
    *   **(Admin)** Sends a test welcome message to the configured welcome channel.
*   `/autoassignroles`
    *   **(Admin)** Configures roles that users can self-assign using buttons or reactions attached to a specific message.
*   `/setannouncementchannel`
    *   **(Admin)** Designates the current channel for the announcement system. Messages sent here by admins can be formatted into embeds and optionally DM'd to users.
*   `/optout_dms`
    *   Allows users to opt-out of receiving direct messages from the announcement system.
*   `/optin_dms`
    *   Allows users to opt back into receiving direct messages from the announcement system.

---

**Community & Engagement**

*   `/ticket`
    *   **(Admin)** Creates the ticket panel message in the current channel. Users click a dropdown to open tickets based on predefined categories.
*   `/transcripts`
    *   **(Admin)** Sets the current channel as the destination for ticket transcripts and user feedback.
*   `/linktwitch`
    *   Links your Discord account to your Twitch username to enable \"Go Live\" notifications.
    *   *Usage:* `/linktwitch twitch_username:<YourTwitchName>`
*   `/unlinktwitch`
    *   Unlinks your Twitch account.
*   `/twitchnotificationchannel`
    *   **(Admin)** Sets the channel where Twitch \"Go Live\" notifications will be posted.
    *   *Usage:* `/twitchnotificationchannel channel:<#Channel>`
*   `/giveaway`
    *   **(Admin)** Starts a giveaway.
    *   *Usage:* `/giveaway duration:<Time> winners:<Number> prize:<Text> [channel:<#Channel>] [required_role:<@Role>]`
*   `/reroll`
    *   **(Admin)** Rerolls winners for a completed giveaway message.
    *   *Usage:* `/reroll message_id:<MessageID> [winners:<Number>]`
*   `/endgiveaway`
    *   **(Admin)** Ends an active giveaway early.
    *   *Usage:* `/endgiveaway message_id:<MessageID>`
*   `/level`
    *   Check your (or another user's) current XP level and progress in the server.
    *   *Usage:* `/level [user:<@User>]`
*   `/leaderboard`
    *   Displays the server's XP leaderboard.
*   `/give_exp`
    *   **(Admin)** Manually gives XP to a user.
    *   *Usage:* `/give_exp user:<@User> amount:<Number>`
*   `/blackjack`
    *   Starts a game of Blackjack (if enabled). Requires currency/points system integration.
*   `/crash`
    *   Starts a game of Crash (if enabled). Requires currency/points system integration.

---

**Utility**

*   `/message`
    *   **(Staff)** Sends a direct message to a user via the bot, with options for acknowledgment/read receipts.
    *   *Usage:* `/message user:<@User> message:<Text>`
*   `/setmessage`
    *   **(Admin)** Sets the current channel to receive message acknowledgment logs from the `/message` command.

---

## Automatic Behaviors & Background Tasks

Beyond the slash commands, the bot performs several actions automatically based on events or schedules:

*   **XP System:**
    *   **Message XP:** Awards a small amount of XP for messages sent (with a cooldown per user).
    *   **Voice XP:** Awards XP periodically to users actively participating in voice channels (excluding AFK channels).
    *   **XP Decay:** Regularly removes a small amount of XP from users who have been inactive (haven't gained XP) for a configured period (e.g., 5 hours), encouraging continued participation.
    *   **Role Updates:** Automatically assigns or removes level-specific roles based on a user's current XP level whenever their XP changes.

*   **Rank Role Updates:**
    *   Periodically (e.g., every 30 minutes), the bot fetches the latest rank information for linked users from THE FINALS API and automatically updates their corresponding rank roles in Discord.

*   **Moderator Point Tracking:**
    *   **Voice Activity:** Awards points to moderators for time spent in designated voice channels.
    *   **Poll Activity:** Automatically awards points when moderators create or participate in Discord polls.
    *   **Inactivity Penalty:** Deducts points from moderators if they haven't earned any points within a 24-hour period.
    *   **Weekly Improvement Bonus:** Awards bonus points to moderators whose weekly point total is higher than the previous week.
    *   **Weekly Reports:** Sends a direct message to each moderator weekly summarizing their earned points.

*   **Ban System:**
    *   **Expiration Check:** Regularly checks for bans that have reached their expiration time, automatically unbanning the user in Discord and removing the ban record.
    *   **Message Blocking:** Deletes messages sent by users who are currently banned within the server.
    *   **Join Check:** If a user joins while having an active ban record, the bot reapplies channel restrictions.

*   **Twitch Integration:**
    *   **Stream Monitoring:** Periodically checks the status of linked Twitch channels.
    *   **Live Role & Notification:** When a linked user goes live, the bot assigns the "Live on Twitch" role and sends a notification message to the configured channel. It removes the role when the stream ends.

*   **Giveaway Management:**
    *   **End Check:** Monitors active giveaways and automatically ends them when their duration expires, selecting winners and updating the giveaway message.
    *   **Time Update:** Periodically updates the "Time Remaining" display on active giveaway embeds.

*   **Welcome Messages:**
    *   Sends a customizable welcome message and/or image to a designated channel when a new member joins the server.

*   **Announcements:**
    *   Listens for messages sent by administrators in the designated announcement channel, automatically formats them into embeds, and optionally DMs them to server members (respecting opt-outs).

*   **Server Logging:**
    *   Listens for various server events (member join/leave/update, message edit/delete, role changes, channel changes, etc.) and logs them to a designated channel if enabled.

*   **Auto-Assign Roles:**
    *   Handles button clicks or dropdown selections on role-assignment messages to add/remove roles.
    *   Listens for direct messages containing verification proof (screenshots/stats) in response to role verification requests.

*   **Message Acknowledgment:**
    *   Monitors DMs sent via the `/message` command for a specific reaction (`✅`) to confirm the user has seen it, logging the acknowledgment.

*   **Warning Acknowledgment:**
    *   Periodically checks if users have acknowledged warnings that require it, potentially sending reminders.

---

## Contact

Discord: Jakubi.

If you use my bot or code make sure to credit me.
