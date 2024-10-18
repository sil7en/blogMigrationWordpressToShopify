# Blog migration from Wordpress to Shopify

## Dependencies

- Wordpress -> WPGraphQL Plugin
Obtain endpoint from plugin (default https://domain.com/graphql)

- Shopify API
Create custom app and obtain API Token

## Create and activate virtual env

```bash
python -m venv venv
source venv/Scripts/activate
```

## Install dependencies
```bash
pip install -r requirements.txt
```

## Create .env file

It should contain:

SHOPIFY_STORE=your-store.myshopify.com
SHOPIFY_API_TOKEN=your_shopify_api_token
WPGRAPHQL_ENDPOINT=https://your-wordpress-site.com/graphql