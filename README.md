# Ruby Grind Bot

A multi-functional Discord bot designed for communities centered around the game "THE FINALS". It offers features for player stats tracking, server moderation, community engagement, and more.

## Features

*   **THE FINALS Integration:**
    *   Player stats lookup (`/gamestats`).
    *   Leaderboard tracking.
    *   Automatic rank role assignment based on in-game rank (`/task_rank_roles`).
    *   Map rotation tracking and prediction embed (`/maprotation`).
    *   Account linking between Discord and THE FINALS (`/link_setup`, `/force_link`).
*   **Moderation:**
    *   Timed bans with appeal system (`/ban`, `/unban`).
    *   Warning system with tracking, clearing, and disagreement tickets (`/warn`, `/warnings`, `/clearwarning`).
    *   Message management: pruning, channel locking, slowmode (`/prune`, `/lock`, `/unlock`, `/slowmode`).
    *   Moderator activity point tracking (`/modleaderboard`, `/checkmod`, `/givepoints`).
    *   Restrict user actions.
*   **Server Management:**
    *   Configurable feature management via `/setup`.
    *   Welcome channel messages (`/welcomechannel`).
    *   Auto-role assignment via reactions/buttons (`/autoassignroles`).
    *   Professional announcement formatting (`/setannouncementchannel`).
    *   Server logging (integrated into various modules).
*   **Community & Engagement:**
    *   Support ticket system with categories, transcripts, and feedback (`/ticket`, `/transcripts`).
    *   Twitch "Go Live" notifications and role assignment (`/linktwitch`, `/unlinktwitch`, `/twitchnotificationchannel`).
    *   Giveaway system (`/giveaway`).
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
    *   View your linked THE FINALS leaderboard statistics. Requires account linking first.
*   `/rank`
    *   Displays a user's current THE FINALS rank (requires account linking).
*   `/task_rank_roles`
    *   **(Admin)** Manages the automatic assignment of roles based on player ranks fetched from THE FINALS API.
*   `/maprotation`
    *   Displays the current predicted map rotation embed. (Setup might be needed via `/maprotation setup`).
*   `/link_setup`
    *   **(Admin)** Sets up the channel for the account verification/linking embed.
    *   Users interact with buttons ("Verify", "Delete Link") in the embed posted by this command to link/unlink their Discord account to their THE FINALS in-game name.
*   `/force_link`
    *   **(Staff)** Manually links a Discord user to a THE FINALS in-game name.
    *   *Usage:* `/force_link user:<@User> in_game_name:<Name#0000>`

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
    *   **(Admin - Manage Channels)** Locks or unlocks the current channel.
*   `/slowmode`
    *   **(Admin - Manage Channels)** Sets slowmode in the current channel.
    *   *Usage:* `/slowmode seconds:<Number>`
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

*   `/welcomechannel`
    *   **(Admin)** Sets up or manages the channel where welcome messages are sent.
*   `/autoassignroles`
    *   **(Admin)** Configures roles that users can self-assign using buttons or reactions attached to a specific message.
*   `/setannouncementchannel`
    *   **(Admin)** Designates the current channel for the announcement system. Messages sent here by admins can be formatted into embeds and optionally DM'd to users.

---

**Community & Engagement**

*   `/ticket`
    *   **(Admin)** Creates the ticket panel message in the current channel. Users click a dropdown to open tickets based on predefined categories.
*   `/transcripts`
    *   **(Admin)** Sets the current channel as the destination for ticket transcripts and user feedback.
*   `/linktwitch`
    *   Links your Discord account to your Twitch username to enable "Go Live" notifications.
    *   *Usage:* `/linktwitch twitch_username:<YourTwitchName>`
*   `/unlinktwitch`
    *   Unlinks your Twitch account.
*   `/twitchnotificationchannel`
    *   **(Admin)** Sets the channel where Twitch "Go Live" notifications will be posted.
    *   *Usage:* `/twitchnotificationchannel channel:<#Channel>`
*   `/giveaway`
    *   **(Admin)** Starts a giveaway.
    *   *Usage:* `/giveaway duration:<Time> winners:<Number> prize:<Text> [channel:<#Channel>] [required_role:<@Role>]`
*   `/level`
    *   Check your (or another user's) current XP level and progress in the server.
    *   *Usage:* `/level [user:<@User>]`
*   `/leaderboard`
    *   Displays the server's XP leaderboard.
*   `/give_exp`
    *   **(Admin)** Manually gives XP to a user.
    *   *Usage:* `/give_exp user:<@User> amount:<Number>`
*   `/blackjack`
    *   Starts a game of Blackjack (if enabled).
*   `/crash`
    *   Starts a game of Crash (if enabled).

---

**Utility**

*   `/message`
    *   **(Staff)** Sends a direct message to a user via the bot, with options for acknowledgment/read receipts.
    *   *Usage:* `/message user:<@User> message:<Text>`
*   `/setmessage`
    *   **(Admin)** Sets the current channel to receive message acknowledgment logs from the `/message` command.

---



## Contact

Discord: Jakubi.

If you use my bot or code make sure to credit me.
