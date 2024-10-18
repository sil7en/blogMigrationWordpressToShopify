import os
import sys
import requests
from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import base64
import logging
from requests_toolbelt import MultipartEncoder
from PIL import Image
from io import BytesIO
import time

# Configurar logging
logging.basicConfig(filename='migration.log', level=logging.DEBUG, format='%(asctime)s %(levelname)s:%(message)s')

# Cargar variables de entorno
load_dotenv()

# Variables de entorno
SHOPIFY_STORE = os.getenv('SHOPIFY_STORE')  # e.g., 'tu-tienda.myshopify.com'
SHOPIFY_API_TOKEN = os.getenv('SHOPIFY_API_TOKEN')
WPGRAPHQL_ENDPOINT = os.getenv('WPGRAPHQL_ENDPOINT')  # e.g., 'https://tu-dominio-wordpress.com/graphql'

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

def upload_image_to_shopify(image_url):
    try:
        logging.debug(f"Descargando imagen: {image_url}")
        # Descargar imagen
        image_response = requests.get(image_url)
        image_response.raise_for_status()
        image_data = image_response.content

        # Optimizar imagen
        image = Image.open(BytesIO(image_data))
        image_format = image.format
        image_io = BytesIO()
        image.save(image_io, format=image_format, optimize=True)
        optimized_image_data = image_io.getvalue()

        # Codificar imagen en base64
        encoded_image = base64.b64encode(optimized_image_data).decode('utf-8')

        # Obtener nombre de archivo
        filename = image_url.split('/')[-1]

        # Preparar payload
        payload = {
            "file": {
                "attachment": encoded_image,
                "filename": filename
            }
        }

        # Subir imagen a Shopify
        response = shopify_request(
            'POST',
            f"https://{SHOPIFY_STORE}/admin/api/2023-10/files.json",
            json=payload
        )
        logging.debug(f"Respuesta de la API al subir imagen: {response.status_code} - {response.text}")
        if response.status_code == 201:
            file_url = response.json()['file']['url']
            return file_url
        else:
            logging.error(f"Error al subir imagen {filename}: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logging.error(f"Excepción al subir imagen {image_url}: {str(e)}")
        return None

def process_images_in_content(content):
    soup = BeautifulSoup(content, 'html.parser')
    images = soup.find_all('img')
    for img in images:
        src = img.get('src')
        if src:
            logging.debug(f"Procesando imagen en contenido: {src}")
            new_src = upload_image_to_shopify(src)
            if new_src:
                img['src'] = new_src
            else:
                logging.error(f"No se pudo subir la imagen {src}")
    return str(soup)

def transform_content(content):
    # Eliminar shortcodes y realizar otras transformaciones
    soup = BeautifulSoup(content, 'html.parser')

    # Eliminar shortcodes de WordPress
    for shortcode in soup.find_all('div', class_='shortcode'):
        shortcode.decompose()

    # Actualizar enlaces internos
    for link in soup.find_all('a', href=True):
        href = link['href']
        if 'tu-dominio-wordpress.com' in href:
            # Reemplazar con la estructura de URLs de Shopify
            new_href = href.replace('tu-dominio-wordpress.com', SHOPIFY_STORE)
            link['href'] = new_href
            logging.debug(f"Enlace actualizado: {href} -> {new_href}")

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

def migrate_posts():
    # Obtener el ID del blog en Shopify
    blog_id = get_shopify_blog_id()
    if not blog_id:
        logging.error("No se puede continuar sin el ID del blog.")
        return

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
    max_posts = 2  # Cambia este valor para migrar más entradas

    while has_next_page and migrated_posts < max_posts:
        variables = {'first': 10, 'after': after}
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
                author = post['author']['node']['name'] if post['author'] else 'Desconocido'
                categories = [cat['name'] for cat in post['categories']['nodes']]
                tags = [tag['name'] for tag in post['tags']['nodes']]
                featured_image_url = post['featuredImage']['node']['sourceUrl'] if post['featuredImage'] else None

                logging.debug(f"Procesando entrada: {title}")

                # Transformar el contenido
                content = transform_content(content)

                # Procesar imágenes en el contenido
                content = process_images_in_content(content)

                # Subir imagen destacada
                if featured_image_url:
                    new_featured_image_url = upload_image_to_shopify(featured_image_url)
                else:
                    new_featured_image_url = None

                # Preparar payload para la entrada
                article_payload = {
                    "article": {
                        "title": title,
                        "author": author,
                        "body_html": content,
                        "published_at": date,
                        "tags": ', '.join(categories + tags),
                    }
                }

                # Agregar imagen destacada
                if new_featured_image_url:
                    article_payload['article']['image'] = {
                        "src": new_featured_image_url
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

                    # Crear redirección desde la URL antigua a la nueva URL de Shopify
                    old_url = f"/{slug}/"
                    new_url = f"/blogs/{article['blog']['handle']}/{handle}"
                    redirect_payload = {
                        "redirect": {
                            "path": old_url,
                            "target": new_url
                        }
                    }
                    redirect_response = shopify_request(
                        'POST',
                        f"https://{SHOPIFY_STORE}/admin/api/2023-10/redirects.json",
                        json=redirect_payload
                    )
                    logging.debug(f"Respuesta de la API al crear redirección: {redirect_response.status_code} - {redirect_response.text}")
                    if redirect_response.status_code == 201:
                        logging.info(f"Redirección creada: {old_url} -> {new_url}")
                    else:
                        logging.error(f"Error al crear redirección para '{title}': {redirect_response.status_code} - {redirect_response.text}")

                    migrated_posts += 1
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
