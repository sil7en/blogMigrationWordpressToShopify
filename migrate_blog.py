import os
import sys
import requests
from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import logging
import time

# Configurar logging
logging.basicConfig(filename='migration.log', level=logging.DEBUG, format='%(asctime)s %(levelname)s:%(message)s')

# Cargar variables de entorno
load_dotenv()

# Variables de entorno
SHOPIFY_STORE = os.getenv('SHOPIFY_STORE')  # Por ejemplo, 'tu-tienda.myshopify.com'
SHOPIFY_API_TOKEN = os.getenv('SHOPIFY_API_TOKEN')
WPGRAPHQL_ENDPOINT = os.getenv('WPGRAPHQL_ENDPOINT')  # Por ejemplo, 'https://tu-dominio-wordpress.com/graphql'

# Verificar que todas las variables estén definidas
if not all([SHOPIFY_STORE, SHOPIFY_API_TOKEN, WPGRAPHQL_ENDPOINT]):
    logging.error("Faltan una o más variables de entorno.")
    sys.exit("Por favor, asegúrate de que SHOPIFY_STORE, SHOPIFY_API_TOKEN y WPGRAPHQL_ENDPOINT están definidas en tu archivo .env.")

# Configurar cliente WPGraphQL sin obtener el esquema
wp_transport = RequestsHTTPTransport(
    url=WPGRAPHQL_ENDPOINT,
    use_json=True,
    timeout=30,
    retries=3,
)
wp_client = Client(transport=wp_transport, fetch_schema_from_transport=False)

# Encabezados para la API de Shopify
shopify_headers = {
    'Content-Type': 'application/json',
    'X-Shopify-Access-Token': SHOPIFY_API_TOKEN
}

# Manejo de límites de tasa para la API de Shopify
def shopify_request(method, url, **kwargs):
    response = requests.request(method, url, headers=shopify_headers, **kwargs)
    while response.status_code == 429:
        # Límite de tasa excedido, esperar y reintentar
        retry_after = int(response.headers.get('Retry-After', 5))
        logging.warning(f"Límite de tasa excedido. Reintentando después de {retry_after} segundos.")
        time.sleep(retry_after)
        response = requests.request(method, url, headers=shopify_headers, **kwargs)
    return response

def get_blog_handle(blog_id):
    response = shopify_request(
        'GET',
        f"https://{SHOPIFY_STORE}/admin/api/2023-10/blogs/{blog_id}.json"
    )
    if response.status_code == 200:
        blog = response.json()['blog']
        return blog['handle']
    else:
        logging.error(f"Error al obtener el handle del blog: {response.status_code} - {response.text}")
        return None  # Manejar el error adecuadamente

def transform_content(content):
    # Eliminar shortcodes y realizar otras transformaciones
    soup = BeautifulSoup(content, 'html.parser')

    # Eliminar shortcodes de WordPress
    for shortcode in soup.find_all('div', class_='shortcode'):
        shortcode.decompose()

    # Nota: Se ha eliminado el código que actualizaba los enlaces internos

    return str(soup)

def get_shopify_blog_id():
    response = shopify_request(
        'GET',
        f"https://{SHOPIFY_STORE}/admin/api/2023-10/blogs.json"
    )
    logging.debug(f"Respuesta de la API al obtener blogs: {response.status_code} - {response.text}")
    if response.status_code == 200:
        blogs = response.json()['blogs']
        if blogs:
            # Usar el primer blog
            blog_id = blogs[0]['id']
            logging.debug(f"ID del blog obtenido: {blog_id}")
            return blog_id
        else:
            # Crear un nuevo blog
            payload = {
                "blog": {
                    "title": "Blog"
                }
            }
            response = shopify_request(
                'POST',
                f"https://{SHOPIFY_STORE}/admin/api/2023-10/blogs.json",
                json=payload
            )
            logging.debug(f"Respuesta de la API al crear blog: {response.status_code} - {response.text}")
            if response.status_code == 201:
                blog_id = response.json()['blog']['id']
                logging.debug(f"Nuevo ID del blog: {blog_id}")
                return blog_id
            else:
                logging.error(f"Error al crear el blog: {response.status_code} - {response.text}")
                return None
    else:
        logging.error(f"Error al obtener blogs: {response.status_code} - {response.text}")
        return None

def get_existing_shopify_articles(blog_id):
    existing_slugs = set()
    page_info = {'has_next_page': True, 'end_cursor': None}
    limit = 250
    since_id = None

    while True:
        params = {'limit': limit}
        if since_id:
            params['since_id'] = since_id

        response = shopify_request(
            'GET',
            f"https://{SHOPIFY_STORE}/admin/api/2023-10/blogs/{blog_id}/articles.json",
            params=params
        )
        if response.status_code == 200:
            articles = response.json()['articles']
            if not articles:
                break
            for article in articles:
                existing_slugs.add(article['handle'])
            since_id = articles[-1]['id']
        else:
            logging.error(f"Error al obtener artículos existentes: {response.status_code} - {response.text}")
            break

    return existing_slugs

def migrate_posts():
    # Obtener el ID del blog en Shopify
    blog_id = get_shopify_blog_id()
    if not blog_id:
        logging.error("No se puede continuar sin el ID del blog.")
        return

    # Obtener el handle del blog
    blog_handle = get_blog_handle(blog_id)
    if not blog_handle:
        logging.error("No se puede continuar sin el handle del blog.")
        return

    # Obtener los slugs de artículos existentes en Shopify
    existing_slugs = get_existing_shopify_articles(blog_id)
    logging.debug(f"Slugs de artículos existentes: {existing_slugs}")

    # Definir la consulta GraphQL para obtener las entradas
    query = gql('''
    query GetPosts($first: Int!, $after: String) {
        posts(first: $first, after: $after) {
            pageInfo {
                hasNextPage
                endCursor
            }
            nodes {
                id
                title
                content
                date
                slug
                excerpt
                author {
                    node {
                        name
                    }
                }
                categories {
                    nodes {
                        name
                    }
                }
                tags {
                    nodes {
                        name
                    }
                }
                featuredImage {
                    node {
                        sourceUrl
                    }
                }
            }
        }
    }
    ''')

    # Variables para la consulta
    has_next_page = True
    after = None
    migrated_posts = 0
    max_posts = 999999  # Para pruebas iniciales

    while has_next_page and migrated_posts < max_posts:
        variables = {'first': 1, 'after': after}  # Procesar uno por uno
        try:
            result = wp_client.execute(query, variable_values=variables)
            posts = result['posts']['nodes']
            page_info = result['posts']['pageInfo']
            has_next_page = page_info['hasNextPage']
            after = page_info['endCursor']
        except Exception as e:
            logging.error(f"Error al obtener entradas de WPGraphQL: {str(e)}")
            break

        for post in posts:
            if migrated_posts >= max_posts:
                break
            try:
                title = post['title']
                content = post['content']
                date = post['date']
                slug = post['slug']
                excerpt = post.get('excerpt', '')
                # Generar meta descripción
                meta_description = BeautifulSoup(excerpt, 'html.parser').get_text().strip()
                if not meta_description:
                    content_text = BeautifulSoup(content, 'html.parser').get_text()
                    meta_description = content_text[:160]
                author = post['author']['node']['name'] if post['author'] else 'Desconocido'
                categories = [cat['name'] for cat in post['categories']['nodes']]
                tags = [tag['name'] for tag in post['tags']['nodes']]
                featured_image_url = post['featuredImage']['node']['sourceUrl'] if post['featuredImage'] else None

                logging.debug(f"Procesando entrada: {title}")

                # Verificar si el artículo ya existe
                if slug in existing_slugs:
                    logging.info(f"Entrada '{title}' ya existe. Saltando.")
                    continue

                # Transformar el contenido
                content = transform_content(content)

                # Preparar payload para el artículo
                article_payload = {
                    "article": {
                        "title": title,
                        "author": author,
                        "body_html": content,
                        "published_at": date,
                        "tags": ', '.join(categories + tags),
                        "summary_html": meta_description,
                        "handle": slug,  # Usar el slug como handle
                    }
                }

                # Agregar imagen destacada usando src
                if featured_image_url:
                    article_payload['article']['image'] = {
                        "src": featured_image_url
                    }

                logging.debug(f"Payload de la entrada: {article_payload}")

                # Crear la entrada en Shopify
                response = shopify_request(
                    'POST',
                    f"https://{SHOPIFY_STORE}/admin/api/2023-10/blogs/{blog_id}/articles.json",
                    json=article_payload
                )
                logging.debug(f"Respuesta de la API al crear entrada: {response.status_code} - {response.text}")
                if response.status_code == 201:
                    article = response.json()['article']
                    article_id = article['id']
                    handle = article['handle']
                    logging.info(f"Entrada '{title}' creada exitosamente con ID {article_id}.")

                    # Agregar slug a los existentes para evitar duplicados
                    existing_slugs.add(slug)

                    migrated_posts += 1

                    # Retraso después de procesar cada entrada
                    time.sleep(5)  # Ajusta el tiempo según tus necesidades

                else:
                    logging.error(f"Error al crear la entrada '{title}': {response.status_code} - {response.text}")
                    continue

            except Exception as e:
                logging.error(f"Excepción al procesar la entrada '{title}': {str(e)}")
                continue

    logging.info(f"Migración completada. Total de entradas migradas: {migrated_posts}")

if __name__ == "__main__":
    try:
        migrate_posts()
    except Exception as e:
        logging.error(f"Error inesperado: {str(e)}")
