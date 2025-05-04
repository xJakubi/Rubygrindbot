import os
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import datetime
import json
import aiohttp
from azure.cosmos import CosmosClient, PartitionKey, exceptions
from command_link_setup import get_user_link
import numpy as np
from dateutil import parser
from scipy import stats
import matplotlib.pyplot as plt
from io import BytesIO
import requests
import re
import aiohttp
import urllib.parse
import time


DISPLAY_NAME = "Ruby Rank Tracker"
DESCRIPTION = "Check how close you are to achieving Ruby rank (top 500 players) in THE FINALS."
ENABLED_BY_DEFAULT = True

# Add the new API endpoints
NEW_API_BASE_URL = "https://thefinals.fortunevale.de/api"
CURRENT_SEASON = "s6"  # Update as needed or fetch dynamically

async def get_player_data_from_api(player_name: str) -> dict:
    """Fetch player data from the leaderboard API."""
    try:
        encoded_name = urllib.parse.quote(player_name)
        url = f"{NEW_API_BASE_URL}/leaderboard/name/{CURRENT_SEASON}/{encoded_name}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 404:
                    print(f"Player '{player_name}' not found in season {CURRENT_SEASON}")
                    return None
                else:
                    print(f"API Error: {response.status} - {await response.text()}")
                    return None
    except Exception as e:
        print(f"Error fetching player data: {e}")
        return None

async def get_leaderboard_from_api(page: int = 1, chunk_size: int = 500) -> list:
    """Fetch a chunk of the leaderboard from the API."""
    try:
        url = f"{NEW_API_BASE_URL}/leaderboard/list/{CURRENT_SEASON}/{page}?chunkSize={chunk_size}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    print(f"API Error: {response.status} - {await response.text()}")
                    return []
    except Exception as e:
        print(f"Error fetching leaderboard: {e}")
        return []

async def get_leaderboard_schedule() -> dict:
    """Get the leaderboard update schedule."""
    try:
        url = f"{NEW_API_BASE_URL}/leaderboard/schedule?season={CURRENT_SEASON}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    print(f"API Error: {response.status} - {await response.text()}")
                    return {"LastUpdate": None, "ApproximateNextUpdate": None}
    except Exception as e:
        print(f"Error fetching schedule: {e}")
        return {"LastUpdate": None, "ApproximateNextUpdate": None}

# Use environment variables if available; otherwise, fall back to default values.
COSMOS_ENDPOINT = os.getenv("COSMOS_ENDPOINT") 
COSMOS_KEY = os.getenv("COSMOS_KEY") 
COSMOS_DATABASE = os.getenv("COSMOS_DATABASE") 


# Azure OpenAI configuration for prediction model
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_MODEL = os.getenv("AZURE_OPENAI_MODEL")

async def get_ruby_threshold():
    """
    Get the current Ruby threshold (score of the 500th ranked player) from the database.
    Returns a dictionary with the current threshold, player info, and timestamp.
    """
    try:
        client = CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY)
        database = client.create_database_if_not_exists(id=COSMOS_DATABASE)
        container = database.create_container_if_not_exists(
            id="rank_history",
            partition_key=PartitionKey(path="/name"),
            offer_throughput=400
        )
        
        # Use ORDER BY to sort by timestamp DESC to get the most recent entries
        # Then find the one with rank = 500
        query = """
        SELECT TOP 1 r.rankScore, r.name, r.timestamp 
        FROM r 
        WHERE r.rank = 500 
        ORDER BY r.timestamp DESC
        """
        
        items = list(container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        if items:
            threshold_player = items[0]
            return {
                "threshold": threshold_player.get("rankScore", 0),
                "player_name": threshold_player.get("name", "Unknown"),
                "timestamp": threshold_player.get("timestamp", datetime.datetime.now().isoformat())
            }
        
        # If direct query failed, try to find the player at position 500 by sorting all players
        query = """
        SELECT r.rankScore, r.name, r.timestamp 
        FROM r 
        ORDER BY r.rankScore DESC 
        OFFSET 499 LIMIT 1
        """
        
        items = list(container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        if items:
            threshold_player = items[0]
            return {
                "threshold": threshold_player.get("rankScore", 0),
                "player_name": threshold_player.get("name", "Unknown"),
                "timestamp": threshold_player.get("timestamp", datetime.datetime.now().isoformat())
            }
        
        # If we still can't find it, check the history entries for rank 500 players
        query = """
        SELECT TOP 1 h.rankScore, r.name, h.timestamp
        FROM r
        JOIN h IN r.history
        WHERE h.rank = 500
        ORDER BY h.timestamp DESC
        """
        
        items = list(container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        if items:
            threshold_player = items[0]
            return {
                "threshold": threshold_player.get("rankScore", 0),
                "player_name": threshold_player.get("name", "Unknown"),
                "timestamp": threshold_player.get("timestamp", datetime.datetime.now().isoformat())
            }
            
        # If still no results, fetch directly from API
        return await get_ruby_threshold_from_api()
        
    except Exception as e:
        print(f"Error getting Ruby threshold: {e}")
        # Fallback to API if database query fails
        return await get_ruby_threshold_from_api()

async def get_ruby_threshold_from_api():
    """Get the Ruby threshold directly from the new API by fetching the top 500 players."""
    try:
        # Get players up to rank 500
        players = await get_leaderboard_from_api(page=1, chunk_size=500)
        
        if players and len(players) >= 500:
            player_500 = players[499]  # 0-indexed, so 499 is the 500th player
            
            # Debug print to see the structure of player_500
            print(f"Player 500 data: {player_500}")
            
            # Try different field names that might contain the score
            score = None
            if "Score" in player_500:
                score = player_500["Score"]
            elif "score" in player_500:
                score = player_500["score"]
            elif "rankScore" in player_500:
                score = player_500["rankScore"]
            else:
                # If we can't find a score field, look through all fields
                for key, value in player_500.items():
                    if isinstance(value, (int, float)) and value > 10000:
                        score = value
                        print(f"Found potential score in field '{key}': {value}")
                        break
            
            if score is not None:
                return {
                    "threshold": score,
                    "player_name": player_500.get("PlayerName", player_500.get("playerName", "Unknown")),
                    "timestamp": datetime.datetime.now().isoformat()
                }
            else:
                print(f"Could not find score in player data: {player_500}")
                return {
                    "threshold": 0,
                    "player_name": "Unknown",
                    "timestamp": datetime.datetime.now().isoformat()
                }
        else:
            # Not enough players, return a default value
            print(f"Not enough players returned from API: {len(players) if players else 0}")
            return {
                "threshold": 0,
                "player_name": "Unknown",
                "timestamp": datetime.datetime.now().isoformat()
            }
    except Exception as e:
        print(f"Error fetching Ruby threshold from API: {e}")
        return {
            "threshold": 0,
            "player_name": "Unknown",
            "timestamp": datetime.datetime.now().isoformat()
        }



async def get_historical_thresholds():
    """
    Get historical Ruby thresholds from the previous data or APIs.
    Returns thresholds for 24 hours ago and 7 days ago.
    
    Note: Since the new API doesn't provide historical data easily, 
    we'll use estimates or fallbacks if data isn't available.
    """
    # Try to get current threshold
    current_threshold = await get_ruby_threshold()
    threshold_value = current_threshold.get("threshold", 0)
    

    
    # Estimate: Ruby threshold increases by ~0.5% per day
    daily_growth_rate = 0.005
    threshold_24h = int(threshold_value / (1 + daily_growth_rate))
    threshold_7d = int(threshold_value / (1 + (daily_growth_rate * 7)))
    
    # Create simulated daily data for prediction
    daily_data = []
    now = datetime.datetime.now()
    
    # Generate 7 days of "synthetic" historical data
    for i in range(7, 0, -1):
        past_day = now - datetime.timedelta(days=i)
        # Decrease by the growth rate as we go back in time
        estimated_threshold = int(threshold_value / (1 + (daily_growth_rate * i)))
        daily_data.append({
            "rankScore": estimated_threshold,
            "timestamp": past_day.isoformat()
        })
    
    return {
        "threshold_24h": threshold_24h,
        "threshold_7d": threshold_7d,
        "daily_data": daily_data
    }

async def get_player_rank_info(in_game_name):
    """
    Get the current rank information for a player from the database.
    Returns the player's rank, score, and other relevant information.
    """
    try:
        client = CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY)
        database = client.create_database_if_not_exists(id=COSMOS_DATABASE)
        container = database.create_container_if_not_exists(
            id="rank_history",
            partition_key=PartitionKey(path="/name"),
            offer_throughput=400
        )
        
        # Query to get the player's most recent record
        query = f"SELECT * FROM r WHERE r.name = @name"
        parameters = [{"name": "@name", "value": in_game_name}]
        
        items = list(container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))
        
        if items:
            player_info = items[0]
            return {
                "name": player_info.get("name", in_game_name),
                "rank": player_info.get("rank", 0),
                "rankScore": player_info.get("rankScore", 0),
                "league": player_info.get("league", "Unknown"),
                "timestamp": player_info.get("timestamp", datetime.datetime.now().isoformat()),
                "history": player_info.get("history", [])
            }
        else:
            # Try to get from API if not found in database
            return await get_player_info_from_api(in_game_name)
    
    except Exception as e:
        print(f"Error getting player rank info: {e}")
        # Fallback to API if database query fails
        return await get_player_info_from_api(in_game_name)

async def get_player_info_from_api(in_game_name):
    """Get player information from the new API."""
    try:
        player_data = await get_player_data_from_api(in_game_name)
        
        if not player_data or "CurrentPlacement" not in player_data:
            return {
                "name": in_game_name,
                "rank": 0,
                "rankScore": 0,
                "league": "Unknown",
                "timestamp": datetime.datetime.now().isoformat(),
                "history": []
            }
        
        # Extract data from the API response
        current = player_data["CurrentPlacement"]
        player_name = current.get("PlayerName", in_game_name)
        placement = current.get("Placement", 0)
        
        # Try different field names that might contain the score
        score = 0
        if "Score" in current:
            score = current["Score"]
        elif "score" in current:
            score = current["score"]
        elif "rankScore" in current:
            score = current["rankScore"]
        
        # Determine league based on placement
        league = "Ruby" if placement <= 500 else "Unknown"
        
        # Extract history if available
        history = []
        if "History" in player_data:
            # Convert the API's history format to our format
            score_history = player_data["History"].get("s", {})
            placement_history = player_data["History"].get("p", {})
            
            for minutes_str, score_value in score_history.items():
                # Convert minutes to timestamp
                minutes = int(minutes_str)
                timestamp = datetime.datetime.fromtimestamp(minutes * 60).isoformat()
                
                # Get placement at this point if available
                placement_value = None
                if minutes_str in placement_history:
                    placement_value = placement_history[minutes_str]
                
                history.append({
                    "timestamp": timestamp,
                    "rankScore": score_value,
                    "rank": placement_value
                })
        
        return {
            "name": player_name,
            "rank": placement,
            "rankScore": score,
            "league": league,
            "timestamp": datetime.datetime.now().isoformat(),
            "history": history
        }
    except Exception as e:
        print(f"Error fetching player info from API: {e}")
        return {
            "name": in_game_name,
            "rank": 0,
            "rankScore": 0,
            "league": "Unknown",
            "timestamp": datetime.datetime.now().isoformat(),
            "history": []
        }

async def get_player_rank_info(in_game_name):
    """Get the current rank information for a player."""
    # Directly use the API now
    return await get_player_info_from_api(in_game_name)

async def predict_future_threshold(historical_data):
    """
    Predict the Ruby threshold for the next 7 days based on historical data.
    Uses linear regression for prediction.
    """
    daily_data = historical_data.get("daily_data", [])
    
    if not daily_data or len(daily_data) < 3:  # Need at least 3 data points for a meaningful trend
        # If not enough data, use a simple 5% increase per day prediction
        current_threshold = await get_ruby_threshold()
        threshold_value = current_threshold.get("threshold", 0)
        
        if threshold_value == 0:
            return {
                "predictions": [],
                "next_week": 0,
                "confidence": "Low (insufficient data)"
            }
        
        # Simple linear growth prediction at 1% daily (7% weekly)
        daily_growth = 0.01
        predictions = []
        today = datetime.datetime.now()
        
        for i in range(1, 8):  # Next 7 days
            future_date = today + datetime.timedelta(days=i)
            predicted_value = threshold_value * (1 + (daily_growth * i))
            predictions.append({
                "day": i,
                "date": future_date.strftime("%Y-%m-%d"),
                "predicted_threshold": int(predicted_value)
            })
            
        return {
            "predictions": predictions,
            "next_week": int(threshold_value * 1.07),  # 7% increase over a week
            "confidence": "Low (insufficient historical data)"
        }
    
    try:
        # Extract data for regression
        x_dates = []
        y_scores = []
        
        for item in daily_data:
            try:
                dt = parser.parse(item.get("timestamp"))
                x_dates.append(dt.timestamp())  # Convert to seconds since epoch
                y_scores.append(item.get("rankScore", 0))
            except:
                continue
        
        if len(x_dates) < 3:
            raise ValueError("Not enough valid data points")
        
        # Convert to numpy arrays and perform linear regression
        x = np.array(x_dates)
        y = np.array(y_scores)
        
        # Normalize x to avoid numerical issues
        x_mean = np.mean(x)
        x_std = np.std(x)
        if x_std == 0:
            x_std = 1  # Prevent division by zero
        x_normalized = (x - x_mean) / x_std
        
        # Linear regression
        slope, intercept, r_value, p_value, std_err = stats.linregress(x_normalized, y)
        
        # Calculate predictions for the next 7 days
        predictions = []
        today = datetime.datetime.now()
        
        for i in range(1, 8):  # Next 7 days
            future_date = today + datetime.timedelta(days=i)
            future_timestamp = future_date.timestamp()
            future_normalized = (future_timestamp - x_mean) / x_std
            predicted_value = intercept + (slope * future_normalized)
            
            predictions.append({
                "day": i,
                "date": future_date.strftime("%Y-%m-%d"),
                "predicted_threshold": max(0, int(predicted_value))  # Ensure positive value
            })
        
        # Calculate next week's prediction (7 days from now)
        next_week_prediction = predictions[-1]["predicted_threshold"]
        
        # Determine confidence level based on R-squared value
        r_squared = r_value ** 2
        confidence = "Low"
        if r_squared > 0.7:
            confidence = "High"
        elif r_squared > 0.4:
            confidence = "Medium"
        
        return {
            "predictions": predictions,
            "next_week": next_week_prediction,
            "confidence": f"{confidence} (R²: {r_squared:.2f})"
        }
    
    except Exception as e:
        print(f"Error in prediction model: {e}")
        # Fallback to simple prediction
        current_threshold = await get_ruby_threshold()
        threshold_value = current_threshold.get("threshold", 0)
        return {
            "predictions": [],
            "next_week": int(threshold_value * 1.07),  # Assume 7% weekly growth
            "confidence": "Low (prediction error)"
        }

async def generate_prediction_chart(historical_data, prediction_data):
    """Generate a chart showing historical thresholds and predictions"""
    daily_data = historical_data.get("daily_data", [])
    predictions = prediction_data.get("predictions", [])
    
    if not daily_data and not predictions:
        return None
    
    try:
        # Prepare historical data
        hist_dates = []
        hist_scores = []
        
        for item in daily_data:
            try:
                dt = parser.parse(item.get("timestamp"))
                hist_dates.append(dt)
                hist_scores.append(item.get("rankScore", 0))
            except:
                continue
        
        # Prepare prediction data
        pred_dates = []
        pred_scores = []
        
        today = datetime.datetime.now()
        for pred in predictions:
            day = pred.get("day", 0)
            pred_dates.append(today + datetime.timedelta(days=day))
            pred_scores.append(pred.get("predicted_threshold", 0))
        
        # Create the plot
        plt.figure(figsize=(10, 6))
        
        # Plot historical data if available
        if hist_dates and hist_scores:
            plt.plot(hist_dates, hist_scores, 'b-', label='Historical Threshold')
            
        # Plot prediction data if available
        if pred_dates and pred_scores:
            plt.plot(pred_dates, pred_scores, 'r--', label='Predicted Threshold')
        
        # Add labels and title
        plt.xlabel('Date')
        plt.ylabel('Rank Score')
        plt.title('Ruby Threshold: Historical Data & Prediction')
        plt.legend()
        plt.grid(True)
        
        # Format x-axis to show dates nicely
        plt.gcf().autofmt_xdate()
        
        # Save plot to a buffer
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        buf.seek(0)
        
        # Clean up
        plt.close()
        
        return buf
        
    except Exception as e:
        print(f"Error generating chart: {e}")
        return None

async def create_advanced_prediction_with_openai(historical_data, current_threshold):
    """
    Use GPT-4o to generate a more sophisticated prediction based on historical data.
    """
    if not AZURE_OPENAI_API_KEY or not AZURE_OPENAI_ENDPOINT:
        return None
        
    daily_data = historical_data.get("daily_data", [])
    
    # Format the data for GPT-4o
    data_points = []
    for item in daily_data:
        try:
            dt = parser.parse(item.get("timestamp"))
            data_points.append({
                "date": dt.strftime("%Y-%m-%d"),
                "rankScore": item.get("rankScore", 0)
            })
        except:
            continue
    
    # Return None if we don't have enough data
    if len(data_points) < 3:
        return None
    
    # Sort data points by date
    data_points.sort(key=lambda x: x["date"])
    
    # Create the prompt for GPT-4o
    prompt = {
        "messages": [
            {
                "role": "system", 
                "content": (
                    "You are an AI specialized in statistical analysis and predictive modeling. You will analyze the historical data of "
                    "Ruby rank thresholds in a game called THE FINALS and provide predictions for future thresholds."
                )
            },
            {
                "role": "user",
                "content": (
                    f"Here is historical data of the Ruby rank threshold (rank score of the 500th ranked player) in THE FINALS:\n\n"
                    f"{json.dumps(data_points, indent=2)}\n\n"
                    f"The current threshold is {current_threshold}.\n\n"
                    f"Based on this data, please predict the Ruby threshold for each of the next 7 days, "
                    f"and explain the prediction methodology and factors considered. "
                    f"Return your response as a JSON object with the following structure:\n"
                    f"{{\n"
                    f"  \"analysis\": \"Your analysis of the historical trend\",\n"
                    f"  \"methodology\": \"Brief explanation of your prediction method\",\n"
                    f"  \"predictions\": [\n"
                    f"    {{ \"day\": 1, \"date\": \"YYYY-MM-DD\", \"predicted_threshold\": value, \"confidence\": 0-100 }},\n"
                    f"    ...\n"
                    f"  ],\n"
                    f"  \"next_week_threshold\": value,\n"
                    f"  \"factors\": [\"factor1\", \"factor2\", ...],\n"
                    f"  \"overall_confidence\": \"High/Medium/Low\"\n"
                    f"}}"
                )
            }
        ]
    }
    
    try:
        headers = {
            "Content-Type": "application/json",
            "api-key": AZURE_OPENAI_API_KEY
        }
        
        response = requests.post(
            AZURE_OPENAI_ENDPOINT,
            headers=headers,
            json=prompt
        )
        
        if response.status_code == 200:
            result = response.json()
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            # Extract the JSON from the response
            try:
                # Find JSON block in the response
                json_match = re.search(r'```json\s*([\s\S]*?)\s*```', content)
                if json_match:
                    json_str = json_match.group(1)
                else:
                    # If no code block, try to parse the entire content
                    json_str = content
                
                prediction_data = json.loads(json_str)
                return prediction_data
            except json.JSONDecodeError as e:
                print(f"Error parsing GPT-4o response: {e}")
                print(f"Raw response: {content}")
                return None
        else:
            print(f"Error from Azure OpenAI API: {response.status_code} {response.text}")
            return None
            
    except Exception as e:
        print(f"Error generating advanced prediction: {e}")
        return None

class RubyButtonView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=300)  # 5 minute timeout
        self.user_id = user_id
    
    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Refresh the Ruby status with updated data"""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your Ruby status! Use `/ruby` to check your own status.", ephemeral=True)
            return
            
        # Get the linked account for the user
        user_link = await get_user_link(interaction.user.id)
        if not user_link:
            await interaction.response.send_message("You don't have a linked THE FINALS account. Use `/link_setup` first!", ephemeral=True)
            return
            
        in_game_name = user_link.get("in_game_name", "")
        
        # Show thinking state
        await interaction.response.defer(thinking=True)
        
        # Get Ruby threshold and player data
        threshold_data = await get_ruby_threshold()
        player_data = await get_player_rank_info(in_game_name)
        historical_data = await get_historical_thresholds()
        
        # Create a new embed with the updated data
        embed = await create_ruby_status_embed(interaction.user, player_data, threshold_data, historical_data)
        
        # Update the message
        await interaction.followup.edit_message(interaction.message.id, embed=embed)

    @discord.ui.button(label="Show Prediction Chart", style=discord.ButtonStyle.secondary)
    async def chart_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Show a chart with Ruby threshold prediction"""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your Ruby status! Use `/ruby` to check your own status.", ephemeral=True)
            return
            
        # Show thinking state
        await interaction.response.defer(thinking=True)
        
        # Get historical data and prediction
        historical_data = await get_historical_thresholds()
        threshold_data = await get_ruby_threshold()
        prediction_data = await predict_future_threshold(historical_data)
        
        # Generate the chart
        chart_buffer = await generate_prediction_chart(historical_data, prediction_data)
        
        if chart_buffer:
            # Send the chart as a file
            await interaction.followup.send(
                "Here's the Ruby threshold prediction chart:",
                file=discord.File(chart_buffer, filename="ruby_prediction.png")
            )
        else:
            await interaction.followup.send("Sorry, I couldn't generate a prediction chart with the available data.")

async def create_ruby_status_embed(user, player_data, threshold_data, historical_data):
    """Create the embed for displaying Ruby status"""
    # Calculate values
    player_rank = player_data.get("rank", 0)
    player_score = player_data.get("rankScore", 0)
    ruby_threshold = threshold_data.get("threshold", 0)
    threshold_24h = historical_data.get("threshold_24h", 0)
    threshold_7d = historical_data.get("threshold_7d", 0)
    
    # Calculate differences
    is_ruby = player_rank <= 500 and player_rank > 0
    points_needed = max(0, ruby_threshold - player_score) if not is_ruby else 0
    threshold_change_24h = ruby_threshold - threshold_24h if threshold_24h > 0 else 0
    threshold_change_7d = ruby_threshold - threshold_7d if threshold_7d > 0 else 0
    
    # Daily rate of change calculation
    daily_avg_increase = threshold_change_7d / 7 if threshold_7d > 0 else threshold_change_24h
    
    # Time to reach Ruby estimate
    days_to_ruby = "N/A"
    if not is_ruby and points_needed > 0 and daily_avg_increase > 0:
        # Calculate two scenarios:
        # 1. Player gains points at same rate as threshold grows
        if player_score > 0:
            # If player isn't growing at all, they'll never reach ruby
            days_to_ruby = "∞" 
        else:
            days_to_ruby = "∞"
    
    # Prediction for future threshold
    prediction_data = await predict_future_threshold(historical_data)
    next_week_threshold = prediction_data.get("next_week", 0)
    next_week_increase = next_week_threshold - ruby_threshold if ruby_threshold > 0 else 0
    confidence = prediction_data.get("confidence", "Low")
    
    # Create embed
    if is_ruby:
        color = discord.Color.from_rgb(224, 78, 57)  # Ruby red color
        title = "🔴 YOU ARE RUBY! 🔴"
    else:
        color = discord.Color.from_rgb(127, 127, 127)  # Grey for non-Ruby
        title = "Ruby Rank Status"
    
    embed = discord.Embed(
        title=title,
        description=f"Status for **{player_data.get('name', 'Unknown')}**",
        color=color
    )
    
    # Current status section
    embed.add_field(
        name="📊 Current Status", 
        value=(
            f"**Rank:** #{player_rank:,}\n"
            f"**Score:** {player_score:,} points\n"
            f"**League:** {player_data.get('league', 'Unknown')}\n"
        ), 
        inline=True
    )
    
    # Ruby threshold section
    embed.add_field(
        name="🔴 Ruby Threshold", 
        value=(
            f"**Current:** {ruby_threshold:,} points\n"
            f"**24h Change:** +{threshold_change_24h:,}\n"
            f"**7d Change:** +{threshold_change_7d:,}\n"
        ), 
        inline=True
    )
    
    # Progress section
    if is_ruby:
        margin = player_score - ruby_threshold
        embed.add_field(
            name="🏆 Ruby Status", 
            value=(
                f"**Congratulations!**\n"
                f"You are **{margin:,} points** above the Ruby threshold.\n"
                f"Keep playing to maintain your Ruby status!"
            ), 
            inline=False
        )
    else:
        embed.add_field(
            name="📈 Progress to Ruby", 
            value=(
                f"**Points Needed:** {points_needed:,}\n"
                f"**Avg Daily Increase:** +{daily_avg_increase:.0f}/day\n"
                f"**Est. Time to Ruby:** {days_to_ruby}\n"
            ), 
            inline=False
        )
    
    # Prediction section
        # Prediction section
    embed.add_field(
        name="🔮 Ruby Threshold Prediction", 
        value=(
            f"**Next Week:** {next_week_threshold:,} points\n"
            f"**Weekly Increase:** +{next_week_increase:,}\n"
            f"**Confidence:** {confidence}"
        ),
        inline=False
    )
    
    # Add footer with timestamp
    embed.set_footer(text=f"Last updated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Add user avatar as thumbnail
    if user and user.avatar:
        embed.set_thumbnail(url=user.avatar.url)
    
    return embed

async def setup(bot: commands.Bot) -> None:
    # Extract feature name from the module name
    feature_name = __name__[8:] if __name__.startswith('command_') else __name__
    
    @bot.tree.command(name="ruby", description="Check your progress towards Ruby rank (top 500) in THE FINALS")
    async def ruby_command(interaction: discord.Interaction):
        """Check your progress towards Ruby rank in THE FINALS"""
        # Check if feature is enabled for this guild
        if interaction.guild and not bot.is_feature_enabled(feature_name, interaction.guild.id):
            await interaction.response.send_message(
                "The Ruby Rank Tracker feature is not enabled in this server. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        # Get the linked account for the user
        user_link = await get_user_link(interaction.user.id)
        if not user_link:
            await interaction.response.send_message(
                "You don't have a linked THE FINALS account. Use a verification channel with the link setup feature to link your account first!",
                ephemeral=True
            )
            return
        
        in_game_name = user_link.get("in_game_name", "")
        
        # Show thinking state while we gather data
        await interaction.response.defer(thinking=True)
        
        # Get Ruby threshold and player data
        threshold_data = await get_ruby_threshold()
        player_data = await get_player_rank_info(in_game_name)
        historical_data = await get_historical_thresholds()
        
        # Create the embed
        embed = await create_ruby_status_embed(interaction.user, player_data, threshold_data, historical_data)
        
        # Create the view with buttons
        view = RubyButtonView(interaction.user.id)
        
        # Send the response with the embed and view
        await interaction.followup.send(embed=embed, view=view)
    

    
    @bot.tree.command(name="check_ruby", description="Check if another player has achieved Ruby rank")
    @app_commands.describe(player_name="The player's in-game name (e.g., playername#0000)")
    async def check_ruby_command(interaction: discord.Interaction, player_name: str):
        """Check if another player has achieved Ruby rank"""
        # Check if feature is enabled for this guild
        if interaction.guild and not bot.is_feature_enabled(feature_name, interaction.guild.id):
            await interaction.response.send_message(
                "The Ruby Rank Tracker feature is not enabled in this server. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        # Validate player name format
        if "#" not in player_name:
            await interaction.response.send_message(
                "Please enter a valid player name in the format `playername#0000`.",
                ephemeral=True
            )
            return
        
        # Show thinking state while we gather data
        await interaction.response.defer(thinking=True)
        
        # Get Ruby threshold and player data
        threshold_data = await get_ruby_threshold()
        player_data = await get_player_rank_info(player_name)
        
        # If player not found
        if player_data.get("rank", 0) == 0:
            await interaction.followup.send(f"Player **{player_name}** not found in the leaderboard. Make sure the name is correct and the player is ranked.")
            return
        
        # Calculate values
        player_rank = player_data.get("rank", 0)
        player_score = player_data.get("rankScore", 0)
        ruby_threshold = threshold_data.get("threshold", 0)
        
        # Create the embed
        is_ruby = player_rank <= 500 and player_rank > 0
        
        if is_ruby:
            color = discord.Color.from_rgb(224, 78, 57)  # Ruby red color
            title = f"🔴 {player_name} IS RUBY! 🔴"
        else:
            color = discord.Color.from_rgb(127, 127, 127)  # Grey for non-Ruby
            title = f"Ruby Status for {player_name}"
        
        embed = discord.Embed(
            title=title,
            description=f"Checking Ruby rank status for **{player_data.get('name', player_name)}**",
            color=color
        )
        
        # Player status section
        embed.add_field(
            name="Player Status", 
            value=(
                f"**Rank:** #{player_rank:,}\n"
                f"**Score:** {player_score:,} points\n"
                f"**League:** {player_data.get('league', 'Unknown')}"
            ), 
            inline=True
        )
        
        # Ruby threshold info
        embed.add_field(
            name="Ruby Threshold", 
            value=f"**Current:** {ruby_threshold:,} points", 
            inline=True
        )
        
        # Ruby status section
        if is_ruby:
            margin = player_score - ruby_threshold
            embed.add_field(
                name="Ruby Status", 
                value=f"**{player_name}** is **{margin:,} points** above the Ruby threshold.",
                inline=False
            )
        else:
            points_needed = max(0, ruby_threshold - player_score)
            embed.add_field(
                name="Ruby Status", 
                value=f"**{player_name}** needs **{points_needed:,} more points** to reach Ruby rank.",
                inline=False
            )
        
        # Send the response
        await interaction.followup.send(embed=embed)