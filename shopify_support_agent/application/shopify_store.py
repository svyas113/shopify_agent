"""ShopifyStore context representing a connected Shopify store."""

from .shopify_client import ShopifyClient
from typing import Optional, ClassVar
import json
import os

class ShopifyStore:
    """
    Represents a Shopify store connection.
    This is the main context for all customer support commands.
    """
    
    # Default instance for singleton-like access
    _default_instance: ClassVar[Optional["ShopifyStore"]] = None

    def __init__(
        self,
        shop_domain: str,
        access_token: str,
        store_name: Optional[str] = None
    ):
        self.shop_domain = shop_domain
        self.store_name = store_name or shop_domain.replace('.myshopify.com', '')
        self.client = ShopifyClient(shop_domain, access_token)

    def __repr__(self):
        return f"ShopifyStore({self.store_name})"

    @classmethod
    def load_from_config(cls, config_path: Optional[str] = None) -> "ShopifyStore":
        """
        Load ShopifyStore configuration from a JSON file.
        
        Args:
            config_path: Path to config file. If None, looks for config.json 
                        in the same directory as this file.
        
        Returns:
            A ShopifyStore instance configured from the JSON file
        """
        if config_path is None:
            # Get the directory where this file is located
            current_dir = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.join(current_dir, "config.json")
        
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        return cls(
            shop_domain=config["shop_domain"],
            access_token=config["access_token"],
            store_name=config.get("store_name")
        )

    @classmethod
    def get_default_instance(cls) -> "ShopifyStore":
        """
        Get a default ShopifyStore instance loaded from config.json.
        This method implements a singleton pattern, ensuring only one
        default instance is created.
        
        Returns:
            A ShopifyStore instance with credentials from config.json
        """
        if cls._default_instance is None:
            # Load from config file instead of hardcoding
            cls._default_instance = cls.load_from_config()
        
        return cls._default_instance