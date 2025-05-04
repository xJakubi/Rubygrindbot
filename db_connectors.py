import os
import asyncio
from azure.cosmos import CosmosClient, exceptions
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class CosmosDBConnector:
    def __init__(self):
        # Get connection string from environment variables
        cosmos_endpoint = os.getenv("COSMOS_ENDPOINT")
        cosmos_key = os.getenv("COSMOS_KEY")
        database_name = os.getenv("COSMOS_DATABASE")
        
        if not cosmos_endpoint or not cosmos_key or not database_name:
            print("Warning: Missing Cosmos DB credentials in environment variables")
            # You could raise an exception here, or set placeholders for development
        
        # Initialize the Cosmos client
        self.client = CosmosClient(cosmos_endpoint, cosmos_key)
        self.database = self.client.get_database_client(database_name)
        
        # Get container clients
        self.players_container = self.database.get_container_client("players")
        self.rank_history_container = self.database.get_container_client("rank_history")
    
    async def execute_query(self, container, query, parameters=None):
        """Execute a query on a container and return results"""
        try:
            if parameters is None:
                parameters = []
                
            # Run the query in a thread to avoid blocking the event loop
            items = await asyncio.to_thread(
                lambda: list(container.query_items(
                    query=query,
                    parameters=parameters,
                    enable_cross_partition_query=True
                ))
            )
            
            return items
        except Exception as e:
            print(f"Failed to execute query: {e}")
            return []
            
    async def get_player_data(self, name):
        """Get the latest player data for a player by name"""
        query = "SELECT * FROM c WHERE c.name = @name ORDER BY c.timestamp DESC OFFSET 0 LIMIT 1"
        parameters = [{"name": "@name", "value": name}]
        
        items = await self.execute_query(self.players_container, query, parameters)
        return items[0] if items else None
    
    async def get_rank_history(self, name, days=30):
        """Get rank history for a player over time"""
        cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
        
        query = "SELECT * FROM c WHERE c.name = @name AND c.timestamp >= @cutoff ORDER BY c.timestamp"
        parameters = [
            {"name": "@name", "value": name},
            {"name": "@cutoff", "value": cutoff_date}
        ]
        
        return await self.execute_query(self.rank_history_container, query, parameters)
    
    async def get_top_players(self, limit=100):
        """Get the current top players"""
        # Get the most recent timestamp
        latest_query = "SELECT VALUE MAX(c.timestamp) FROM c"
        latest_timestamps = await self.execute_query(self.players_container, latest_query)
        
        latest_timestamp = latest_timestamps[0] if latest_timestamps else None
        
        if not latest_timestamp:
            return []
            
        # Get top players from that timestamp
        query = "SELECT * FROM c WHERE c.timestamp = @timestamp ORDER BY c.rankScore DESC OFFSET 0 LIMIT @limit"
        parameters = [
            {"name": "@timestamp", "value": latest_timestamp},
            {"name": "@limit", "value": limit}
        ]
        
        return await self.execute_query(self.players_container, query, parameters)

# Create a singleton instance to be imported by other modules
db = CosmosDBConnector()