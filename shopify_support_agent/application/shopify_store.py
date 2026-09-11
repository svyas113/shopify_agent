"""ShopifyStore context representing a connected Shopify store."""

from .shopify_client import ShopifyClient
from typing import Optional, ClassVar, List
import json
import os
import asyncio

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
        # Cache of all distinct product_type values in this store.
        # Populated lazily on first access via ensure_product_types_loaded().
        self._product_types: Optional[List[str]] = None

    @property
    def product_types(self) -> List[str]:
        """
        Return the cached list of product types.
        Returns an empty list if not yet loaded (call ensure_product_types_loaded first).
        """
        return self._product_types or []

    def ensure_product_types_loaded(self) -> None:
        """
        Synchronously fetch and cache all product types from the store.
        Safe to call from both sync (__call__) and async (process_command) contexts.
        Only makes the API call once; subsequent calls are no-ops.
        """
        if self._product_types is not None:
            return
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("closed")
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        self._product_types = loop.run_until_complete(
            self.client.get_all_product_types()
        )

    async def ensure_product_types_loaded_async(self) -> None:
        """
        Async version: fetch and cache product types if not yet loaded.
        Call this at the top of any async process_command that needs product types.
        """
        if self._product_types is None:
            self._product_types = await self.client.get_all_product_types()

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