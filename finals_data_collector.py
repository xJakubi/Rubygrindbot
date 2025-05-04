import os
import sys
import json
import logging
import asyncio
import datetime
import aiohttp
import time
import re
from azure.cosmos import CosmosClient, PartitionKey, exceptions
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging with a try-except block to catch any initialization issues
try:
    # Create a logs directory in the script's folder
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    # Configure logging to write to the logs directory
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(os.path.join(log_dir, "finals_data_collector.log"), encoding='utf-8'),
            logging.StreamHandler(sys.stdout)  # Explicitly use stdout
        ]
    )
    logger = logging.getLogger('finals_collector')
    
    # Reduce Azure SDK logging to warnings and errors
    logging.getLogger('azure.core.pipeline.policies.http_logging_policy').setLevel(logging.WARNING)
except Exception as e:
    print(f"Error setting up logging: {e}")
    # Set up a basic fallback logger
    logger = logging.getLogger('finals_collector')
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Cosmos DB Configuration from environment variables
COSMOS_ENDPOINT = os.getenv("COSMOS_ENDPOINT")
COSMOS_KEY = os.getenv("COSMOS_KEY")
DB_NAME = os.getenv("COSMOS_DATABASE", "thefinalsdb")

# Check if credentials are available
if not COSMOS_ENDPOINT or not COSMOS_KEY:
    logger.error("Missing Cosmos DB credentials in environment variables")
    print("Error: Missing Cosmos DB credentials. Please set COSMOS_ENDPOINT and COSMOS_KEY in your .env file")
    sys.exit(1)

# THE FINALS API Configuration
API_ENDPOINT = "https://api.the-finals-leaderboard.com/v1/leaderboard"
LEADERBOARD_VERSION = "s6"  # Update this based on current season
PLATFORM = "crossplay"

# Database structure
CONTAINERS = {
    "rank_history": {
        "id": "rank_history", 
        "partition_key": "/name",
        "unique_keys": []
    }
}

def sanitize_id(text):
    """Sanitize ID to make it Cosmos DB compatible"""
    if not text:
        return f"unknown_{int(datetime.datetime.now().timestamp())}"
    # Remove invalid characters and replace spaces with underscores
    sanitized = re.sub(r'[\\/?#]', '', text)  # Remove chars not allowed in Cosmos DB IDs
    return sanitized

class FinalsDataCollector:
    def __init__(self):
        """Initialize the data collector and connect to Cosmos DB"""
        self.cosmos_client = CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY)
        self.database = None
        self.containers = {}
        self.last_run = datetime.datetime.min
    
    async def setup(self):
        """Set up the database and containers"""
        logger.info("Setting up Cosmos DB...")
        
        # Create database if it doesn't exist
        try:
            self.database = self.cosmos_client.create_database_if_not_exists(id=DB_NAME)
            logger.info(f"Database '{DB_NAME}' ready")
        except exceptions.CosmosHttpResponseError as e:
            logger.error(f"Failed to create database: {e}")
            return False
        
        # Create containers if they don't exist
        for container_id, config in CONTAINERS.items():
            try:
                # Prepare the unique key policy properly
                unique_key_policy = None
                if config["unique_keys"]:
                    unique_key_policy = {"uniqueKeys": [{"paths": paths} for paths in config["unique_keys"]]}
                
                container = self.database.create_container_if_not_exists(
                    id=config["id"],
                    partition_key=PartitionKey(path=config["partition_key"]),
                    unique_key_policy=unique_key_policy
                )
                self.containers[container_id] = container
                logger.info(f"Container '{container_id}' ready")
            except exceptions.CosmosHttpResponseError as e:
                logger.error(f"Failed to create container '{container_id}': {e}")
                return False
        
        return True
    
    async def get_latest_history(self, player_name):
        """Get the latest history entry for a player directly from the database"""
        try:
            container = self.containers.get("rank_history")
            if not container:
                logger.error("Rank history container not initialized")
                return None
            
            # Query to get the specific player's document
            query = "SELECT r.history FROM r WHERE r.name = @name"
            parameters = [{"name": "@name", "value": player_name}]
            
            items = list(container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True
            ))
            
            if items and 'history' in items[0] and len(items[0]['history']) > 0:
                history = items[0]['history']
                # Sort by timestamp and get the latest entry
                latest_entry = sorted(history, key=lambda x: x.get('timestamp', ''), reverse=True)[0]
                return latest_entry
            
            return None
        except Exception as e:
            logger.error(f"Error getting latest history for {player_name}: {str(e)}")
            return None
    
    async def store_rank_history_bulk(self, player_data_list):
        """Store rank history data in bulk for multiple players"""
        if not player_data_list:
            logger.warning("No player data provided for bulk operation")
            return 0, 0
            
        start_time = time.time()
        container = self.containers.get("rank_history")
        if not container:
            logger.error("Rank history container not initialized")
            return 0, 0
        
        # Get existing documents to compare with new data
        player_names = [player.get('name') for player in player_data_list if 'name' in player]
        existing_docs = {}
        
        # Fetch existing documents in smaller batches to avoid query size limitations
        batch_size = 100
        for i in range(0, len(player_names), batch_size):
            batch_names = player_names[i:i+batch_size]
            logger.info(f"Fetching documents for batch {i//batch_size + 1}/{(len(player_names) + batch_size - 1)//batch_size}")
            
            # Use individual queries instead of IN clause
            for name in batch_names:
                try:
                    query = "SELECT * FROM c WHERE c.name = @name"
                    parameters = [{"name": "@name", "value": name}]
                    
                    items = list(container.query_items(
                        query=query,
                        parameters=parameters,
                        enable_cross_partition_query=True
                    ))
                    
                    # Add to our map of existing documents
                    for doc in items:
                        existing_docs[doc['name']] = doc
                except Exception as e:
                    logger.error(f"Error querying for player {name}: {str(e)}")
        
        # Add debugging for existing documents count
        logger.info(f"Found {len(existing_docs)} existing player documents out of {len(player_names)} players")
        
        # Prepare operations
        operations = []
        successful_updates = 0
        skipped_updates = 0
        current_timestamp = datetime.datetime.now().isoformat()
        
        # Process each player
        for player_data in player_data_list:
            if not player_data or 'name' not in player_data:
                logger.warning("Skipping invalid rank history data (missing name)")
                continue
                    
            player_name = player_data.get('name', '')
            current_rank_score = player_data.get('rankScore', 0)
            
            # Check if player exists in our fetched documents
            if player_name in existing_docs:
                doc = existing_docs[player_name]
                
                # Get the latest history entry
                latest_entry = None
                if 'history' in doc and doc['history'] and len(doc['history']) > 0:
                    try:
                        latest_entry = sorted(doc['history'], key=lambda x: x.get('timestamp', ''), reverse=True)[0]
                    except Exception as e:
                        logger.error(f"Error sorting history for {player_name}: {str(e)}")
                        # Create a default latest entry if sorting fails
                        latest_entry = {}
                
                last_rank_score = latest_entry.get('rankScore') if latest_entry else None
                
                # Only update if the rank score changed
                if last_rank_score is None or current_rank_score != last_rank_score:
                    # Create history entry
                    history_entry = {
                        **{k: v for k, v in player_data.items() if k != 'name' and k != 'id'},
                        "timestamp": current_timestamp
                    }
                    
                    # Add to history array
                    if 'history' not in doc:
                        doc['history'] = []
                    doc['history'].append(history_entry)
                    
                    # Update top-level rank data
                    for k, v in player_data.items():
                        if k != 'id':  # Don't overwrite the document ID
                            doc[k] = v
                    doc["timestamp"] = current_timestamp
                    
                    # Add replace operation with explicit id
                    operations.append(('replace', doc))
                    successful_updates += 1
                else:
                    skipped_updates += 1
            else:
                # Create new document
                new_doc_id = f"{sanitize_id(player_name)}_{int(time.time())}"
                new_doc = {
                    "id": new_doc_id,
                    "name": player_name,
                    "timestamp": current_timestamp,
                    "history": [{
                        **{k: v for k, v in player_data.items() if k != 'name' and k != 'id'},
                        "timestamp": current_timestamp
                    }]
                }
                
                # Copy all player data to the document
                for k, v in player_data.items():
                    if k != 'id':  # Don't overwrite our generated document ID
                        new_doc[k] = v
                
                # Add create operation
                operations.append(('create', new_doc))
                successful_updates += 1
        
        logger.info(f"Prepared {len(operations)} operations: {successful_updates} updates, {skipped_updates} skipped")
        
        if not operations:
            logger.info("No changes to commit to database")
            return successful_updates, skipped_updates
        
        # Execute operations in batches of 10 (smaller batch size for reliability)
        batch_size = 10
        total_success_count = 0
        
        for i in range(0, len(operations), batch_size):
            batch = operations[i:i+batch_size]
            
            try:
                success_count = 0
                batch_start = time.time()
                # Process each operation individually
                for op_type, doc in batch:
                    try:
                        if op_type == 'create':
                            result = container.create_item(body=doc)
                            success_count += 1
                            logger.debug(f"Created document for {doc.get('name')}, id={result.get('id')}")
                        elif op_type == 'replace':
                            # Make sure we have a valid ID
                            if 'id' not in doc:
                                logger.error(f"Missing ID in document for {doc.get('name', 'unknown')}")
                                continue
                            
                            result = container.replace_item(item=doc['id'], body=doc)
                            success_count += 1
                            logger.debug(f"Updated document for {doc.get('name')}, id={result.get('id')}")
                    except exceptions.CosmosHttpResponseError as item_e:
                        logger.error(f"Error processing {op_type} for {doc.get('name', 'unknown')}: {str(item_e)}")
                        # If this was a replace operation that failed, try creating a new document instead
                        if op_type == 'replace':
                            try:
                                # Generate a new ID
                                doc['id'] = f"{sanitize_id(doc.get('name', 'unknown'))}_{int(time.time())}"
                                result = container.create_item(body=doc)
                                success_count += 1
                                logger.info(f"Successfully created new document for {doc.get('name')} after failed update, id={result.get('id')}")
                            except Exception as fallback_e:
                                logger.error(f"Fallback create also failed for {doc.get('name')}: {str(fallback_e)}")
                    
                batch_time = time.time() - batch_start
                total_success_count += success_count
                logger.info(f"Processed batch {i//batch_size + 1}/{(len(operations) + batch_size - 1)//batch_size}: {success_count}/{len(batch)} operations successful in {batch_time:.2f}s")
            except Exception as e:
                logger.error(f"Error processing batch: {str(e)}")
        
        elapsed_time = time.time() - start_time
        logger.info(f"Bulk operation completed in {elapsed_time:.2f} seconds, {total_success_count}/{len(operations)} operations successful")
        return successful_updates, skipped_updates

    async def fetch_and_store_leaderboard(self, limit=10000):
        """Fetch leaderboard data and store it in Cosmos DB"""
        start_time = time.time()
        logger.info(f"Starting leaderboard data collection - {datetime.datetime.now().isoformat()}")
        
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{API_ENDPOINT}/{LEADERBOARD_VERSION}/{PLATFORM}?limit={limit}"
                logger.info(f"Fetching data from {url}")
                
                try:
                    async with session.get(url, timeout=30) as response:
                        if response.status == 200:
                            data = await response.json()
                            players = data.get('data', [])
                            
                            if not players:
                                logger.error("API returned no player data")
                                return False
                            
                            # Log a sample player record for debugging
                            if players:
                                logger.info(f"Sample player data: {json.dumps(players[0])}")
                            
                            logger.info(f"Retrieved {len(players)} players from API")
                            
                            # Send all player data in a single bulk operation
                            successful_updates, skipped_updates = await self.store_rank_history_bulk(players)
                            
                            elapsed_time = time.time() - start_time
                            logger.info(f"Data collection complete - Processed {len(players)} players in {elapsed_time:.2f} seconds")
                            logger.info(f"History updates: {successful_updates}, Skipped (no changes): {skipped_updates}")
                            return successful_updates > 0 or skipped_updates > 0
                            
                        else:
                            logger.error(f"API error: {response.status}")
                            try:
                                error_text = await response.text()
                                logger.error(f"API error details: {error_text}")
                            except Exception as text_e:
                                logger.error(f"Could not read error details: {str(text_e)}")
                            return False
                except asyncio.TimeoutError:
                    logger.error("API request timed out")
                    return False
            
        except Exception as e:
            logger.error(f"Error fetching leaderboard: {str(e)}")
            return False

async def main():
    """Main entry point for the application"""
    try:
        logger.info("Starting THE FINALS Data Collector")
        
        collector = FinalsDataCollector()
        setup_success = await collector.setup()
        
        if not setup_success:
            logger.error("Failed to set up Cosmos DB. Exiting.")
            return
        
        logger.info("Starting THE FINALS data collection service")
        logger.info(f"Collection interval: 15 minutes")
        
        while True:
            # Run the collection
            success = await collector.fetch_and_store_leaderboard()
            
            if not success:
                logger.error("Failed to fetch and store leaderboard data")
                # Wait a shorter period before retrying
                await asyncio.sleep(60)  # Wait 1 minute before retrying
                continue
            
            # Sleep for 15 minutes
            next_run = datetime.datetime.now() + datetime.timedelta(minutes=15)
            logger.info(f"Next collection scheduled for: {next_run}")
            await asyncio.sleep(15 * 60)  # 15 minutes in seconds
            
    except KeyboardInterrupt:
        logger.info("Data collection service stopped by user")
    except Exception as e:
        logger.error(f"Unhandled error in main: {str(e)}")

if __name__ == "__main__":
    try:
        print("Starting THE FINALS Data Collector")
        print("Press Ctrl+C to stop the service")
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nService stopped by user")
    except Exception as e:
        print(f"Fatal error: {str(e)}")
        # Exit with an error code to indicate there was a problem
        sys.exit(1)