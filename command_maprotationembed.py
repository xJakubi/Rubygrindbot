import discord
from discord.ext import commands
from discord import app_commands
import json
import asyncio
from datetime import datetime, timedelta
import os
from collections import defaultdict

# Module metadata - used by the bot to display information
DISPLAY_NAME = "Map Rotation Tracker"
DESCRIPTION = "Track and predict THE FINALS map rotation patterns"
ENABLED_BY_DEFAULT = True

# Maps in THE FINALS
MAPS = {
    "1": "Fortune Stadium",
    "2": "SYS$HORIZON",
    "3": "Bernal 2024",
    "4": "Skyway Stadium",
    "5": "Kyoto 1568",
    "6": "Las Vegas 2032",
    "7": "Monaco 2014",
    "8": "Seoul 2023"
}

# Store message ID and channel ID for updating the embed
map_rotation_data = {
    "message_id": None,
    "channel_id": None,
    "reports": [],  # Will store map reports with timestamps
    "last_update": None,
    "rotation_pattern": [],  # Will store detected pattern
    "next_maps": [],  # Will store predicted next maps
    "rotation_time": 600,  # Default rotation time in seconds (10 minutes)
    "pattern_confidence": 0.0,  # Confidence level in detected pattern
    "reference_timestamp": None,  # Timestamp for anchoring the rotation
    "verified_pattern": False,  # Whether the pattern is considered verified
    "verification_time": None  # When the pattern was verified
}

# Increased storage - store up to 10000 reports
MAX_REPORTS = 10000

# File to store data between bot restarts
DATA_FILE = "map_rotation_data.json"

def load_rotation_data():
    """Load saved map rotation data from file"""
    global map_rotation_data
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r') as f:
                map_rotation_data = json.load(f)
            print(f"Map rotation data loaded successfully with {len(map_rotation_data['reports'])} reports")
    except Exception as e:
        print(f"Error loading map rotation data: {e}")

def save_rotation_data():
    """Save map rotation data to file"""
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(map_rotation_data, f)
    except Exception as e:
        print(f"Error saving map rotation data: {e}")

def calculate_confidence(pattern, occurrences, total_length):
    """Calculate confidence score for a detected pattern"""
    # Base confidence on pattern length, occurrence frequency, and total coverage
    pattern_length = len(pattern)
    if pattern_length < 2:
        return 0.0
        
    # More weight for longer patterns
    length_factor = min(1.0, pattern_length / 8.0)
    
    # How many times we've seen the pattern repeat
    occurrence_factor = min(1.0, occurrences / 5.0)
    
    # How much of the total data the pattern explains
    coverage = (pattern_length * occurrences) / total_length
    coverage_factor = min(1.0, coverage)
    
    # Combine factors with different weights
    confidence = (length_factor * 0.3) + (occurrence_factor * 0.5) + (coverage_factor * 0.2)
    return min(1.0, confidence) * 100.0

def get_reference_timestamp(pattern, map_clusters, rotation_time):
    """Calculate a reference timestamp for the start of the pattern"""
    if not pattern or not map_clusters:
        return None
        
    # Find most recent occurrence of the first map in the pattern
    first_map = pattern[0]
    
    # Search from most recent to oldest
    for cluster in reversed(map_clusters):
        if cluster["map_id"] == first_map:
            # Calculate when this pattern started
            pattern_position = 0
            return cluster["timestamp"] - (pattern_position * rotation_time)
            
    return None

def analyze_rotation_pattern():
    """Analyze reports to detect rotation pattern and make predictions"""
    # If we already have a verified pattern with high confidence, use it
    if map_rotation_data.get("verified_pattern") and map_rotation_data.get("pattern_confidence", 0) > 90:
        pattern = map_rotation_data.get("rotation_pattern")
        
        if pattern and map_rotation_data.get("reference_timestamp"):
            return analyze_with_verified_pattern(pattern)
    
    # If not enough data, can't analyze
    if not map_rotation_data["reports"] or len(map_rotation_data["reports"]) < 5:
        return None, [], 0.0
    
    # Get reports from the last 600 hours for comprehensive analysis
    recent_time = datetime.utcnow().timestamp() - (600 * 60 * 60)
    recent_reports = [r for r in map_rotation_data["reports"] if r["timestamp"] > recent_time]
    
    # Need at least 20 reports to begin analysis
    if len(recent_reports) < 20:
        return None, [], 0.0
    
    # Sort reports by timestamp
    sorted_reports = sorted(recent_reports, key=lambda r: r["timestamp"])
    
    # Group reports by map and time to find clusters
    map_clusters = []
    current_map = sorted_reports[0]["map_id"]
    current_cluster = [sorted_reports[0]]
    
    for report in sorted_reports[1:]:
        # If this is a different map or more than 3 minutes have passed
        if (report["map_id"] != current_map or 
            report["timestamp"] - current_cluster[-1]["timestamp"] > 180):
            
            # Save the most reported map in this cluster
            map_counts = defaultdict(int)
            for r in current_cluster:
                map_counts[r["map_id"]] += 1
            
            most_common = max(map_counts, key=map_counts.get)
            avg_time = sum(r["timestamp"] for r in current_cluster) / len(current_cluster)
            
            map_clusters.append({
                "map_id": most_common,
                "timestamp": avg_time
            })
            
            # Start new cluster
            current_map = report["map_id"]
            current_cluster = [report]
        else:
            current_cluster.append(report)
    
    # Add the last cluster
    if current_cluster:
        map_counts = defaultdict(int)
        for r in current_cluster:
            map_counts[r["map_id"]] += 1
        
        most_common = max(map_counts, key=map_counts.get)
        avg_time = sum(r["timestamp"] for r in current_cluster) / len(current_cluster)
        
        map_clusters.append({
            "map_id": most_common,
            "timestamp": avg_time
        })
    
    # Need at least 5 clusters to detect a pattern
    if len(map_clusters) < 5:
        return None, [], 0.0
    
    # Extract sequence of maps
    map_sequence = [cluster["map_id"] for cluster in map_clusters]
    
    # Calculate average time between rotations
    time_diffs = []
    for i in range(1, len(map_clusters)):
        diff = map_clusters[i]["timestamp"] - map_clusters[i-1]["timestamp"]
        # Only consider reasonable rotation times (between 5 and 15 minutes)
        if 300 <= diff <= 900:
            time_diffs.append(diff)
    
    # Calculate rotation time, default to 600 seconds (10 min) if no valid data
    if time_diffs:
        avg_rotation_time = sum(time_diffs) / len(time_diffs)
        # Round to nearest 30 seconds for better precision
        avg_rotation_time = round(avg_rotation_time / 30) * 30
        map_rotation_data["rotation_time"] = avg_rotation_time
    else:
        avg_rotation_time = map_rotation_data["rotation_time"]
    
    # Determine current map (most recent cluster)
    current_map_id = map_clusters[-1]["map_id"]
    
    # Try to detect a pattern in the map sequence
    pattern, occurrences = detect_pattern_with_confidence(map_sequence)
    
    # Calculate confidence in this pattern
    confidence = calculate_confidence(pattern, occurrences, len(map_sequence))
    
    # If confidence is high enough, store as verified pattern
    if confidence >= 90 and not map_rotation_data.get("verified_pattern"):
        map_rotation_data["verified_pattern"] = True
        map_rotation_data["verification_time"] = datetime.utcnow().timestamp()
        map_rotation_data["rotation_pattern"] = pattern
        
        # Set reference timestamp for absolute timing
        reference_timestamp = get_reference_timestamp(pattern, map_clusters, avg_rotation_time)
        map_rotation_data["reference_timestamp"] = reference_timestamp
    
    # Store the pattern confidence
    map_rotation_data["pattern_confidence"] = confidence
        
    # Predict next maps
    next_maps = []
    if pattern:
        # Use pattern for prediction
        try:
            # Find where in the pattern the current map is
            current_idx = -1
            for i, map_id in enumerate(pattern):
                if map_id == current_map_id:
                    current_idx = i
                    break
            
            # If current map not found in pattern, try an alternative approach
            if current_idx == -1:
                # Use most common map transitions to predict next
                transitions = {}
                for i in range(len(map_sequence) - 1):
                    pair = (map_sequence[i], map_sequence[i + 1])
                    transitions[pair] = transitions.get(pair, 0) + 1
                
                # Find most likely next map based on transitions
                next_candidates = []
                for (prev, next_map), count in transitions.items():
                    if prev == current_map_id:
                        next_candidates.append((next_map, count))
                
                if next_candidates:
                    # Sort by frequency
                    next_candidates.sort(key=lambda x: x[1], reverse=True)
                    for i in range(1, 4):
                        idx = (i - 1) % len(next_candidates)
                        next_map_id = next_candidates[idx][0]
                        next_maps.append({
                            "map_id": next_map_id,
                            "map_name": MAPS[next_map_id],
                            "estimated_time": map_clusters[-1]["timestamp"] + (avg_rotation_time * i)
                        })
            else:
                # Predict next maps based on pattern
                for i in range(1, 4):
                    next_idx = (current_idx + i) % len(pattern)
                    next_map_id = pattern[next_idx]
                    
                    # Calculate precise time based on rotation
                    estimated_time = map_clusters[-1]["timestamp"] + (avg_rotation_time * i)
                    
                    # If we have a reference timestamp, use it for absolute timing
                    if map_rotation_data.get("reference_timestamp"):
                        ref_time = map_rotation_data["reference_timestamp"]
                        pattern_position = (current_idx + i) % len(pattern)
                        cycles_passed = ((datetime.utcnow().timestamp() - ref_time) // 
                                        (avg_rotation_time * len(pattern)))
                        next_absolute_time = (ref_time + 
                                            (cycles_passed * avg_rotation_time * len(pattern)) + 
                                            (pattern_position * avg_rotation_time))
                        
                        # If this time is in the past, add another cycle
                        if next_absolute_time < datetime.utcnow().timestamp():
                            next_absolute_time += (avg_rotation_time * len(pattern))
                        
                        estimated_time = next_absolute_time
                    
                    next_maps.append({
                        "map_id": next_map_id,
                        "map_name": MAPS[next_map_id],
                        "estimated_time": estimated_time
                    })
        except Exception as e:
            print(f"Error in pattern-based prediction: {e}")
            # Fall back to simple prediction
            pattern = None
    
    # If no pattern or error, use simple sequence prediction
    if not pattern or not next_maps:
        try:
            for i in range(1, 4):
                next_map_id = str((int(current_map_id) % len(MAPS)) + 1)
                current_map_id = next_map_id  # Update for next iteration
                next_maps.append({
                    "map_id": next_map_id,
                    "map_name": MAPS[next_map_id],
                    "estimated_time": map_clusters[-1]["timestamp"] + (avg_rotation_time * i)
                })
        except Exception as e:
            print(f"Error in simple prediction: {e}")
    
    return pattern, next_maps, confidence

def analyze_with_verified_pattern(pattern):
    """Use verified pattern for predictions without reanalyzing"""
    if not pattern:
        return None, [], 0.0
        
    # Get reference timestamp and rotation time
    reference_timestamp = map_rotation_data.get("reference_timestamp")
    rotation_time = map_rotation_data.get("rotation_time", 600)
    confidence = map_rotation_data.get("pattern_confidence", 95.0)
    
    if not reference_timestamp:
        return pattern, [], confidence
    
    # Calculate current position in pattern
    now = datetime.utcnow().timestamp()
    elapsed = now - reference_timestamp
    pattern_cycle_time = rotation_time * len(pattern)
    position_in_cycle = (elapsed % pattern_cycle_time) / rotation_time
    current_idx = int(position_in_cycle)
    
    # Calculate next maps with precise timing
    next_maps = []
    for i in range(1, 4):
        next_idx = (current_idx + i) % len(pattern)
        next_map_id = pattern[next_idx]
        
        # Calculate precise time based on reference
        next_time = reference_timestamp + (
            (elapsed // pattern_cycle_time) * pattern_cycle_time + 
            ((current_idx + i) * rotation_time)
        )
        
        # If this time is in the past, add another cycle
        if next_time < now:
            next_time += pattern_cycle_time
        
        next_maps.append({
            "map_id": next_map_id,
            "map_name": MAPS[next_map_id],
            "estimated_time": next_time
        })
    
    return pattern, next_maps, confidence

def detect_pattern_with_confidence(sequence):
    """Detect repeating pattern in the sequence and count occurrences"""
    if not sequence or len(sequence) < 4:
        return None, 0
    
    best_pattern = None
    most_occurrences = 0
    
    # Try patterns of different lengths
    for pattern_length in range(2, min(9, len(sequence) // 2 + 1)):
        # Look for patterns that repeat multiple times
        for start in range(len(sequence) - pattern_length * 2 + 1):
            candidate = sequence[start:start + pattern_length]
            
            # Count occurrences of this pattern
            occurrences = 0
            for i in range(0, len(sequence) - pattern_length + 1):
                if sequence[i:i + pattern_length] == candidate:
                    occurrences += 1
            
            # Check if this is a good pattern
            if occurrences >= 2:
                # Prioritize longer patterns or patterns with more occurrences
                if (not best_pattern or 
                    len(candidate) > len(best_pattern) or 
                    (len(candidate) == len(best_pattern) and occurrences > most_occurrences)):
                    best_pattern = candidate
                    most_occurrences = occurrences
    
    # If we found a good pattern, return it
    if best_pattern and most_occurrences >= 2:
        return best_pattern, most_occurrences
    
    # As a fallback, check for strict repeating patterns
    for pattern_length in range(2, min(9, len(sequence) // 2 + 1)):
        base_pattern = sequence[:pattern_length]
        repeats = 0
        
        # Count how many full repeats we find
        for i in range(0, len(sequence) - pattern_length + 1, pattern_length):
            if sequence[i:i+pattern_length] == base_pattern:
                repeats += 1
            else:
                break
        
        if repeats >= 2:
            return base_pattern, repeats
    
    return None, 0

class MapSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=f"{map_name}", value=map_id)
            for map_id, map_name in MAPS.items()
        ]
        super().__init__(
            placeholder="Select the map you're currently playing...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="map_select"
        )
    
    async def callback(self, interaction: discord.Interaction):
        selected_map = self.values[0]
        timestamp = datetime.utcnow().timestamp()
        
        # Add the map report with timestamp
        map_rotation_data["reports"].append({
            "map_id": selected_map,
            "map_name": MAPS[selected_map],
            "timestamp": timestamp,
            "reported_by": str(interaction.user.id)
        })
        
        # Keep the most recent MAX_REPORTS reports
        if len(map_rotation_data["reports"]) > MAX_REPORTS:
            map_rotation_data["reports"] = map_rotation_data["reports"][-MAX_REPORTS:]
        
        # Analyze patterns and update predictions
        pattern, next_maps, confidence = analyze_rotation_pattern()
        map_rotation_data["rotation_pattern"] = pattern if pattern else []
        map_rotation_data["next_maps"] = next_maps
        map_rotation_data["pattern_confidence"] = confidence
        map_rotation_data["last_update"] = timestamp
        save_rotation_data()
        
        # Update the embed with the new data
        await update_rotation_embed(interaction.client)
        
        await interaction.response.send_message(
            f"Thanks for reporting that you're playing on {MAPS[selected_map]}!", 
            ephemeral=True
        )

class MapRotationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # Persistent view that doesn't timeout
        self.add_item(MapSelect())

def create_rotation_embed():
    """Create the map rotation embed with current data"""
    embed = discord.Embed(
        title="📍 THE FINALS Map Rotation Tracker",
        description="Help us track the map rotation pattern by selecting the map you're currently playing when you join a match.",
        color=0xff9900
    )
    
    # Get current predicted map and next maps
    pattern, next_maps, confidence = analyze_rotation_pattern()
    
    # Sort reports by timestamp, most recent first
    recent_reports = sorted(
        map_rotation_data["reports"], 
        key=lambda x: x["timestamp"], 
        reverse=True
    )
    
    # Add field for current map
    current_map = "Insufficient data"
    current_map_id = None
    
    # If we have a verified pattern, use it to determine current map
    if map_rotation_data.get("verified_pattern") and pattern:
        ref_timestamp = map_rotation_data.get("reference_timestamp")
        rotation_time = map_rotation_data.get("rotation_time", 600)
        
        if ref_timestamp:
            now = datetime.utcnow().timestamp()
            elapsed = now - ref_timestamp
            pattern_cycle_time = rotation_time * len(pattern)
            position_in_cycle = int((elapsed % pattern_cycle_time) / rotation_time)
            
            current_map_id = pattern[position_in_cycle]
            current_map = MAPS[current_map_id]
            
            # Calculate time left in this map
            time_into_current = elapsed % rotation_time
            time_left = rotation_time - time_into_current
            current_map += f" (changes in {format_seconds(time_left)})"
    # Otherwise use recent reports
    elif recent_reports:
        # Use most common map from last 5 reports
        map_counts = {}
        for report in recent_reports[:5]:
            map_id = report["map_id"]
            map_counts[map_id] = map_counts.get(map_id, 0) + 1
        
        if map_counts:
            # Find the most reported map
            current_map_id = max(map_counts, key=map_counts.get)
            current_map = MAPS[current_map_id]
    
    embed.add_field(name="Current Map", value=current_map, inline=True)
    
    # Add field for next predicted map(s)
    if next_maps:
        next_map = next_maps[0]["map_name"]
        # Format with absolute time
        next_time = next_maps[0]["estimated_time"]
        time_str = format_absolute_time(next_time)
        embed.add_field(name="Next Map", value=f"{next_map} at {time_str}", inline=True)
        
        # If we have additional predictions, add them too
        if len(next_maps) > 1:
            upcoming = []
            for i, map_data in enumerate(next_maps[1:], 2):
                time_str = format_absolute_time(map_data["estimated_time"])
                upcoming.append(f"{i}. {map_data['map_name']} at {time_str}")
            
            if upcoming:
                embed.add_field(
                    name="Upcoming Maps",
                    value="\n".join(upcoming),
                    inline=False
                )
    else:
        embed.add_field(name="Next Map", value="Insufficient data", inline=True)
    
    # Add detected pattern info with confidence
    if pattern:
        pattern_display = " → ".join([MAPS[map_id] for map_id in pattern])
        confidence_status = ""
        
        # Add verification status
        if map_rotation_data.get("verified_pattern"):
            confidence_status = "✅ VERIFIED"
        else:
            confidence_status = f"{confidence:.1f}% confidence"
        
        embed.add_field(
            name=f"Rotation Pattern ({confidence_status})", 
            value=pattern_display,
            inline=False
        )
        
        # If current map is known, show position in cycle
        if current_map_id and current_map_id in pattern:
            # Create visual representation of cycle
            cycle_display = ""
            for i, map_id in enumerate(pattern):
                if map_id == current_map_id:
                    cycle_display += f"**[{MAPS[map_id]}]** → "
                else:
                    cycle_display += f"{MAPS[map_id]} → "
            
            # Remove trailing arrow
            cycle_display = cycle_display.rstrip(" → ")
            
            embed.add_field(
                name="Current Position in Cycle",
                value=cycle_display,
                inline=False
            )
    
    # Add recent reports
    if recent_reports:
        recent_list = []
        for i, report in enumerate(recent_reports[:5]):
            time_ago = format_time_ago(report["timestamp"])
            recent_list.append(f"{report['map_name']} ({time_ago})")
        
        embed.add_field(
            name="Recent Reports", 
            value="\n".join(recent_list) if recent_list else "No reports yet",
            inline=False
        )
    
    # Add stats
    total_reports = len(map_rotation_data["reports"])
    rotation_time = map_rotation_data.get("rotation_time", 600)
    minutes = int(rotation_time / 60)
    
    # Build footer with verification info if available
    footer_text = f"Map rotation data based on {total_reports} player reports • " + \
                 f"Estimated rotation time: ~{minutes} minutes"
                 
    if map_rotation_data.get("verified_pattern") and map_rotation_data.get("verification_time"):
        verify_time = format_time_ago(map_rotation_data["verification_time"])
        footer_text += f" • Pattern verified {verify_time}"
    
    embed.set_footer(text=footer_text)
    
    return embed

def format_seconds(seconds):
    """Format seconds into a readable string"""
    if seconds < 60:
        return f"{int(seconds)} seconds"
    else:
        minutes = int(seconds / 60)
        return f"{minutes} minute{'s' if minutes > 1 else ''}"

def format_time_ago(timestamp):
    """Format timestamp as a human-readable time ago string"""
    now = datetime.utcnow().timestamp()
    diff = now - timestamp
    
    if diff < 60:
        return "just now"
    elif diff < 3600:
        minutes = int(diff / 60)
        return f"{minutes}m ago"
    elif diff < 86400:
        hours = int(diff / 3600)
        return f"{hours}h ago"
    else:
        days = int(diff / 86400)
        return f"{days}d ago"

def format_absolute_time(timestamp):
    """Format timestamp as absolute HH:MM time"""
    dt = datetime.fromtimestamp(timestamp)
    return dt.strftime("%H:%M")

async def update_rotation_embed(bot):
    """Update the existing embed with new data"""
    if map_rotation_data["channel_id"] and map_rotation_data["message_id"]:
        try:
            channel = bot.get_channel(int(map_rotation_data["channel_id"]))
            if not channel:
                channel = await bot.fetch_channel(int(map_rotation_data["channel_id"]))
                if not channel:
                    return
                
            try:
                message = await channel.fetch_message(int(map_rotation_data["message_id"]))
                await message.edit(embed=create_rotation_embed())
            except (discord.NotFound, discord.HTTPException):
                # Message was deleted or couldn't be found, create a new one
                message = await channel.send(embed=create_rotation_embed(), view=MapRotationView())
                map_rotation_data["message_id"] = message.id
                save_rotation_data()
        except Exception as e:
            print(f"Error updating rotation embed: {e}")

async def setup(bot):
    """Set up the map rotation embed command"""
    # Load saved data
    load_rotation_data()
    
    # Define the command
    @bot.tree.command(
        name="maprotationembed",
        description="Create a map rotation tracker embed (Bot owner only)"
    )
    async def maprotationembed_command(interaction: discord.Interaction):
        # Check if user is the bot owner
        if str(interaction.user.id) != "199635181236125697":
            await interaction.response.send_message(
                "Only the bot owner can use this command.",
                ephemeral=True
            )
            return
            
        # Create and send the embed with the view
        embed = create_rotation_embed()
        view = MapRotationView()
        
        await interaction.response.send_message("Creating map rotation tracker...", ephemeral=True)
        message = await interaction.channel.send(embed=embed, view=view)
        
        # Store the message and channel IDs for future updates
        map_rotation_data["message_id"] = message.id
        map_rotation_data["channel_id"] = interaction.channel_id
        save_rotation_data()
    
    # Add a command to reset the pattern (for testing or if something changes)
    @bot.tree.command(
        name="resetpattern",
        description="Reset the verified map rotation pattern (Bot owner only)"
    )
    async def resetpattern_command(interaction: discord.Interaction):
        # Check if user is the bot owner
        if str(interaction.user.id) != "199635181236125697":
            await interaction.response.send_message(
                "Only the bot owner can use this command.",
                ephemeral=True
            )
            return
            
        # Reset verification status
        map_rotation_data["verified_pattern"] = False
        map_rotation_data["pattern_confidence"] = 0.0
        map_rotation_data["reference_timestamp"] = None
        map_rotation_data["verification_time"] = None
        save_rotation_data()
        
        await interaction.response.send_message("Map rotation pattern has been reset.", ephemeral=True)
        
        # Update the embed with the new data
        await update_rotation_embed(interaction.client)

    # Register the persistent view to handle interactions after bot restart
    bot.add_view(MapRotationView())
    
    # Create a background task to periodically update the embed
    from discord.ext import tasks
    
    @tasks.loop(minutes=1)  # Update more frequently for more accurate timing
    async def update_rotation_task():
        await update_rotation_embed(bot)
    
    # Start the task if not already running
    update_rotation_task.start()
    
    # IMPORTANT: Instead of defining on_ready, do an initial update if needed
    if map_rotation_data["channel_id"] and map_rotation_data["message_id"]:
        # Schedule the first update soon after setup
        asyncio.create_task(update_rotation_embed(bot))
            
    # Add a sync command for testing purposes
    @bot.tree.command(
        name="synccommands", 
        description="Sync commands with Discord (Bot owner only)"
    )
    async def sync_command(interaction: discord.Interaction):
        # Check if user is the bot owner
        if str(interaction.user.id) != "199635181236125697":
            await interaction.response.send_message(
                "Only the bot owner can use this command.",
                ephemeral=True
            )
            return
            
        await bot.tree.sync()
        await interaction.response.send_message("Commands synced!", ephemeral=True)
    
    print(f"Map rotation tracker module loaded with {len(map_rotation_data.get('reports', []))} historical reports")