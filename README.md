# Ruby Grind Bot

A Discord bot for THE FINALS community with leaderboard tracking, moderation, and game statistics.

## Features

- THE FINALS player stats and leaderboard tracking
- Server moderation tools (ban, warn, message management)
- Ticket system for support
- Twitch stream notifications
- Giveaway system
- Welcome messages and auto-role assignment
- Game stats and map rotation information
- And more!

## Setup Instructions

1. Clone this repository
2. Create a `.env` file in the root directory with the required environment variables (see `.env.example`)
3. Install dependencies with `pip install -r requirements.txt`
4. Run the bot with `python bot_main.py`

## Environment Variables

Create a `.env` file with the following variables:

```
# Discord Bot Configuration
TOKEN=your_discord_bot_token_here

# Database Configuration
COSMOS_ENDPOINT=your_cosmos_db_endpoint_here
COSMOS_KEY=your_cosmos_db_key_here
COSMOS_DATABASE=your_cosmos_db_name_here

# Azure OpenAI Configuration (if using AI features)
AZURE_OPENAI_ENDPOINT=your_azure_openai_endpoint_here
AZURE_OPENAI_API_KEY=your_azure_openai_api_key_here
AZURE_OPENAI_MODEL=your_azure_openai_model_name
```

## Configuration Files

Several configuration files are needed for the bot to function properly. Example files are provided for reference:

- `guild_settings.json.example` - Server-specific settings
- `ticket_config.json.example` - Support ticket system configuration
- `twitch_links.json.example` - Twitch streamer notification configuration
- `twitch_settings.json.example` - Twitch notification channel configuration
- `active_giveaways.json.example` - Ongoing giveaways data structure

Copy these files without the `.example` extension and configure them for your environment.

## Commands

The bot includes many commands organized in separate modules:

- Moderation: ban, warn, message management, etc.
- Game stats: player ranks, leaderboards, etc.
- Server management: welcome messages, role assignment, etc.
- Entertainment: games, giveaways, etc.

## License

[Insert your preferred license here]

## Contact

[Your contact information or social media links]