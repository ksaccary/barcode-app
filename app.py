from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import requests
import os
import time
from dotenv import load_dotenv
import json
from datetime import datetime, timedelta
from collections import deque
import asyncio
import aiohttp
from urllib.parse import urlencode

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})  # Enable CORS for all routes with all origins

# API Configuration
BARCODE_API_KEY = os.getenv('BARCODE_API_KEY')
BARCODE_API_URL = 'https://barcodereport.com/api'
OPEN_FOOD_FACTS_API = 'https://world.openfoodfacts.org/api/v0'
UPC_DATABASE_API_KEY = os.getenv('UPC_DATABASE_API_KEY')
UPC_DATABASE_API = 'https://api.upcdatabase.org/product'
BARCODE_SPIDER_API_KEY = os.getenv('BARCODE_SPIDER_API_KEY')
BARCODE_SPIDER_API = 'https://api.barcodespider.com/v1/lookup'

# Price API for retail data
PRICE_API_KEY = os.getenv('PRICE_API_KEY')
PRICE_API_URL = 'https://api.priceapi.com/v2'

# Google Custom Search API for shopping results
GOOGLE_SEARCH_API_KEY = os.getenv('GOOGLE_SEARCH_API_KEY')
GOOGLE_SEARCH_CX = os.getenv('GOOGLE_SEARCH_CX')
GOOGLE_SEARCH_API = 'https://www.googleapis.com/customsearch/v1'

# Canadian retailers to specifically look for in Google Shopping results
CANADIAN_RETAILERS = {
    'nofrills.ca': 'No Frills',
    'shoppersdrug': 'Shoppers Drug Mart',
    'atlanticsuperstore': 'Atlantic Superstore',
    'loblaws.ca': 'Loblaws',
    'walmart.ca': 'Walmart Canada',
    'amazon.ca': 'Amazon Canada',
    'canadiantire.ca': 'Canadian Tire',
    'sobeys.com': 'Sobeys',
    'metro.ca': 'Metro',
    'costco.ca': 'Costco Canada',
    'realcanadianstore': 'Real Canadian Superstore'
}

# Exchange rate API
EXCHANGE_RATE_API = f'https://v6.exchangerate-api.com/v6/{os.getenv("EXCHANGE_RATE_API_KEY")}/latest/USD'
exchange_rates_cache = {
    'rates': {},
    'last_updated': None
}

# Rate limiting setup
RATE_LIMIT_PERIOD = 60
MAX_REQUESTS = 30
request_timestamps = deque(maxlen=MAX_REQUESTS)

# Add rate limiting configuration at the top of the file
BARCODE_SPIDER_RATE_LIMIT = 5  # seconds between requests
last_barcode_spider_request = 0

def get_exchange_rates():
    """Get current exchange rates"""
    now = datetime.now()
    
    # Use cached rates if less than 1 hour old
    if (exchange_rates_cache['last_updated'] and 
        (now - exchange_rates_cache['last_updated']).total_seconds() < 3600):
        return exchange_rates_cache['rates']
    
    try:
        response = requests.get(EXCHANGE_RATE_API)
        if response.status_code == 200:
            data = response.json()
            exchange_rates_cache['rates'] = data.get('conversion_rates', {})
            exchange_rates_cache['last_updated'] = now
            return exchange_rates_cache['rates']
    except Exception as e:
        print(f"Error fetching exchange rates: {str(e)}")
    
    # Return default rates if API call fails
    return {'CAD': 1.35}  # Fallback rate

def convert_price_to_cad(price, currency='USD'):
    """Convert price to CAD"""
    if not price:
        return None
        
    try:
        price = float(price)
        if currency == 'CAD':
            return price
            
        rates = get_exchange_rates()
        if currency.upper() in rates:
            # Convert to USD first if not already in USD
            if currency.upper() != 'USD':
                price = price / rates[currency.upper()]
            # Then convert USD to CAD
            return price * rates['CAD']
        else:
            print(f"Currency {currency} not found in exchange rates")
            return price
    except (ValueError, TypeError) as e:
        print(f"Error converting price: {str(e)}")
        return None

def check_rate_limit():
    """Check if we're within rate limits"""
    now = datetime.now()
    while request_timestamps and (now - request_timestamps[0]) > timedelta(seconds=RATE_LIMIT_PERIOD):
        request_timestamps.popleft()
    
    if len(request_timestamps) >= MAX_REQUESTS:
        return False
    return True

async def get_product_from_open_food_facts(session, barcode):
    """Get product information from Open Food Facts"""
    try:
        url = f"{OPEN_FOOD_FACTS_API}/product/{barcode}.json"
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                if data.get('status') == 1:
                    product = data.get('product', {})
                    return {
                        'barcode': barcode,
                        'name': product.get('product_name'),
                        'brand': product.get('brands'),
                        'image_url': product.get('image_url'),
                        'ingredients': product.get('ingredients_text'),
                        'nutrition_grade': product.get('nutrition_grade_fr'),
                        'categories': product.get('categories'),
                        'quantity': product.get('quantity'),
                        'manufacturing_places': product.get('manufacturing_places'),
                        'countries': product.get('countries'),
                        'source': 'Open Food Facts'
                    }
    except Exception as e:
        print(f"Error fetching from Open Food Facts: {str(e)}")
    return None

async def get_product_from_upc_database(session, barcode):
    """Get product information from UPC Database"""
    try:
        # Format barcode to ensure it's clean and properly formatted
        clean_barcode = ''.join(filter(str.isdigit, barcode))
        
        # Ensure the barcode is valid
        if not clean_barcode:
            print(f"Invalid barcode format: {barcode}")
            return None
            
        url = f"{UPC_DATABASE_API}/{clean_barcode}"
        headers = {
            'Authorization': f'Bearer {UPC_DATABASE_API_KEY}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
        
        print(f"Fetching from UPC Database: {url}")
        
        async with session.get(url, headers=headers, ssl=True) as response:
            try:
                response_data = await response.json()
            except Exception as e:
                print(f"Error parsing UPC Database response: {e}")
                return None
                
            print(f"UPC Database raw response: {json.dumps(response_data, indent=2)}")
            
            if response.status == 401:
                print("UPC Database authentication failed. Please check API key.")
                return None
                
            if response.status != 200:
                error_msg = response_data.get('error', {}).get('message', 'Unknown error')
                print(f"UPC Database error: {error_msg}")
                return None
                
            if not response_data.get('success'):
                print(f"UPC Database error: {response_data.get('error', {}).get('message')}")
                return None
                
            product = response_data.get('product', {})
            if not product:
                return None
                
            # Convert price to CAD if available
            price = product.get('price')
            if price:
                try:
                    price = float(price.replace('$', '').replace(',', ''))
                    price_cad = convert_price_to_cad(price, product.get('currency', 'USD'))
                except (ValueError, TypeError):
                    price_cad = None
            else:
                price_cad = None
                
            return {
                'name': product.get('title'),
                'description': product.get('description'),
                'brand': product.get('brand'),
                'manufacturer': product.get('manufacturer'),
                'price': price_cad,
                'currency': 'CAD',
                'image_url': product.get('image'),
                'upc': clean_barcode,
                'category': product.get('category'),
                'mpn': product.get('mpn'),
                'source': 'UPC Database'
            }
                
    except Exception as e:
        print(f"Error fetching from UPC Database: {str(e)}")
    return None

async def get_product_from_barcode_spider(session, barcode):
    """Get product information from Barcode Spider with improved rate limiting"""
    global last_barcode_spider_request
    
    try:
        # Format barcode to ensure it's clean
        clean_barcode = ''.join(filter(str.isdigit, barcode))
        url = f"{BARCODE_SPIDER_API}?upc={clean_barcode}"
        headers = {
            'token': BARCODE_SPIDER_API_KEY,
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
        
        print(f"Fetching from Barcode Spider: {url}")
        
        # Calculate time to wait for rate limiting
        current_time = time.time()
        time_since_last_request = current_time - last_barcode_spider_request
        if time_since_last_request < BARCODE_SPIDER_RATE_LIMIT:
            wait_time = BARCODE_SPIDER_RATE_LIMIT - time_since_last_request
            print(f"Rate limiting: Waiting {wait_time:.2f} seconds before Barcode Spider request")
            await asyncio.sleep(wait_time)
        
        last_barcode_spider_request = time.time()
        
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            async with session.get(url, headers=headers) as response:
                try:
                    response_data = await response.json()
                except Exception as e:
                    print(f"Error parsing Barcode Spider response: {e}")
                    return None
                    
                print(f"Barcode Spider raw response: {json.dumps(response_data, indent=2)}")
                
                if response.status == 429:  # Too Many Requests
                    retry_count += 1
                    if retry_count < max_retries:
                        wait_time = min(BARCODE_SPIDER_RATE_LIMIT * (2 ** retry_count), 15)
                        print(f"Rate limited by Barcode Spider, waiting {wait_time} seconds and retrying... (Attempt {retry_count + 1}/{max_retries})")
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        print("Max retries reached for Barcode Spider")
                        return None
                
                if response.status != 200:
                    print(f"Barcode Spider error: {response_data.get('item_response', {}).get('message')}")
                    return None
                
                product = response_data.get('item_attributes', {})
                if not product:
                    return None
                
                # Process store information
                stores = response_data.get('Stores', [])
                store_details = []
                lowest_price = float('inf')
                
                for store in stores:
                    try:
                        price = float(store.get('price', 0))
                        currency = store.get('currency', 'USD')
                        
                        # Convert price to CAD
                        if currency != 'CAD':
                            price = convert_price_to_cad(price, currency)
                            
                        if price and price < lowest_price:
                            lowest_price = price
                            
                        store_details.append({
                            'store_name': store.get('store_name'),
                            'price': price,
                            'currency': 'CAD',
                            'link': store.get('link'),
                            'last_update': store.get('updated'),
                            'title': store.get('title'),
                            'availability': 'In Stock',
                            'shipping': 'See store for details'
                        })
                    except (ValueError, TypeError) as e:
                        print(f"Error processing store data: {e}")
                        continue
                
                if lowest_price == float('inf'):
                    lowest_price = None
                
                return {
                    'name': product.get('title'),
                    'description': product.get('description'),
                    'brand': product.get('brand'),
                    'manufacturer': product.get('manufacturer'),
                    'price': lowest_price,
                    'currency': 'CAD',
                    'image_url': product.get('image'),
                    'upc': clean_barcode,
                    'ean': product.get('ean'),
                    'category': product.get('category'),
                    'mpn': product.get('mpn'),
                    'model': product.get('model'),
                    'all_stores': store_details,
                    'source': 'Barcode Spider'
                }
                
    except Exception as e:
        print(f"Error fetching from Barcode Spider: {str(e)}")
    return None

async def get_product_from_price_api(session, barcode):
    """Get product information from PriceAPI"""
    try:
        # Updated endpoint and parameters
        url = f"{PRICE_API_URL}/products"  # Changed from /product to /products
        params = {
            'api_key': PRICE_API_KEY,
            'source': 'amazon.ca,walmart.ca,canadiantire.ca,shoppersdrug.ca',
            'country': 'ca',
            'values': barcode,
            'type': 'upc'
        }
        
        print(f"Fetching from PriceAPI: {url}?{urlencode(params)}")
        
        async with session.get(url, params=params) as response:
            response_data = await response.json()
            print(f"PriceAPI response status: {response.status}")
            print(f"PriceAPI response: {json.dumps(response_data, indent=2)}")
            
            if response.status != 200:
                print(f"PriceAPI error: {response_data.get('message', 'Unknown error')}")
                return None
                
            products = response_data.get('products', [])
            if not products:
                return None
            
            product = products[0]  # Get first product match
            
            # Get prices from all retailers
            store_details = []
            lowest_price = float('inf')
            
            for offer in product.get('offers', []):
                try:
                    price = float(offer.get('price', 0))
                    if price and price < lowest_price:
                        lowest_price = price
                        
                    store_details.append({
                        'store_name': offer.get('merchant'),
                        'price': price,
                        'currency': 'CAD',
                        'link': offer.get('link'),
                        'last_update': offer.get('last_updated'),
                        'availability': offer.get('stock_status', 'Unknown'),
                        'shipping': offer.get('shipping_options', 'See store for details')
                    })
                except (ValueError, TypeError) as e:
                    print(f"Error parsing store price: {e}")
                    continue
            
            if lowest_price == float('inf'):
                lowest_price = None
            
            return {
                'name': product.get('title'),
                'description': product.get('description'),
                'brand': product.get('brand'),
                'price': lowest_price,
                'currency': 'CAD',
                'image_url': product.get('image'),
                'upc': barcode,
                'category': product.get('category'),
                'all_stores': store_details,
                'source': 'PriceAPI'
            }
            
    except Exception as e:
        print(f"Error fetching from PriceAPI: {str(e)}")
    return None

async def get_product_from_google_shopping(session, barcode):
    """Get product information from Google Shopping with focus on Canadian retailers"""
    try:
        # Search parameters optimized for Canadian retail results
        params = {
            'key': GOOGLE_SEARCH_API_KEY,
            'cx': GOOGLE_SEARCH_CX,
            'q': f'"{barcode}" OR "UPC {barcode}" site:.ca',  # Focus on Canadian websites with UPC
            'gl': 'ca',  # Geolocation: Canada
            'cr': 'countryCA',  # Country restrict: Canada
            'num': 10,  # Number of results
            'sort': 'date'  # Get most recent results
        }
        
        url = f"{GOOGLE_SEARCH_API}?{urlencode(params)}"
        print(f"Fetching from Google Shopping: {url}")
        
        async with session.get(url) as response:
            response_data = await response.json()
            print(f"Google Shopping response status: {response.status}")
            print(f"Google Shopping response: {json.dumps(response_data, indent=2)}")
            
            if response.status != 200:
                print(f"Google Shopping API error: {response_data.get('error', {}).get('message')}")
                return None
                
            items = response_data.get('items', [])
            if not items:
                print("No items found in Google Shopping results")
                return None
            
            # Process shopping results with focus on Canadian retailers
            store_details = []
            lowest_price = float('inf')
            product_info = None
            
            for item in items:
                try:
                    # Check if the result is from a known Canadian retailer
                    display_link = item.get('displayLink', '').lower()
                    store_name = None
                    
                    # Match against known Canadian retailers
                    for retailer_domain, retailer_name in CANADIAN_RETAILERS.items():
                        if retailer_domain in display_link:
                            store_name = retailer_name
                            break
                    
                    if not store_name:
                        # If not a known retailer but ends in .ca, it's still Canadian
                        if display_link.endswith('.ca'):
                            store_name = display_link.replace('www.', '').capitalize()
                        else:
                            continue  # Skip non-Canadian retailers
                    
                    # Extract price from the shopping result
                    shopping_info = item.get('pagemap', {})
                    price_str = shopping_info.get('offer', [{}])[0].get('price', '0')
                    
                    # Handle various price formats
                    price_str = price_str.replace('$', '').replace(',', '').strip()
                    if not price_str:
                        continue
                        
                    try:
                        price = float(price_str)
                    except ValueError:
                        continue
                    
                    if price and price > 0:  # Ignore invalid prices
                        if price < lowest_price:
                            lowest_price = price
                            product_info = item  # Use this as our main product info
                        
                        # Add store details
                        store_details.append({
                            'store_name': store_name,
                            'price': price,
                            'currency': 'CAD',
                            'link': item.get('link'),
                            'last_update': datetime.now().isoformat(),
                            'title': item.get('title'),
                            'availability': shopping_info.get('offer', [{}])[0].get('availability', 'Unknown'),
                            'condition': shopping_info.get('offer', [{}])[0].get('itemCondition', 'New'),
                            'shipping': shopping_info.get('offer', [{}])[0].get('shippingDetails', 'See store for details')
                        })
                
                except Exception as e:
                    print(f"Error processing Google Shopping item: {e}")
                    continue
            
            if not store_details:
                return None
            
            # Sort stores by price
            store_details.sort(key=lambda x: float(x['price']))
            
            # Extract product details from the best result
            if product_info:
                product_data = product_info.get('pagemap', {})
                product = product_data.get('product', [{}])[0]
                
                return {
                    'name': product.get('name') or product_info.get('title'),
                    'description': product_info.get('snippet'),
                    'brand': product.get('brand'),
                    'price': store_details[0]['price'],  # Lowest price
                    'currency': 'CAD',
                    'image_url': product_data.get('cse_image', [{}])[0].get('src'),
                    'upc': barcode,
                    'category': product.get('category'),
                    'all_stores': store_details,
                    'source': 'Google Shopping'
                }
    
    except Exception as e:
        print(f"Error fetching from Google Shopping: {str(e)}")
    return None

async def get_product_from_barcode_lookup(session, barcode):
    """Get product information from Barcode Lookup website"""
    try:
        url = f"https://www.barcodelookup.com/{barcode}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        print(f"Fetching from Barcode Lookup: {url}")
        
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                print(f"Barcode Lookup error: Status {response.status}")
                return None
                
            html = await response.text()
            
            # Extract product information using string operations
            # These are based on the HTML structure you provided
            try:
                # Extract title
                title = html.split('<h4>')[1].split('</h4>')[0].strip()
                
                # Extract description
                description = html.split('Description: &nbsp;')[1].split('</span>')[0].strip()
                
                # Extract manufacturer
                manufacturer = html.split('Manufacturer: <span class="product-text">')[1].split('</span>')[0].strip()
                
                # Extract brand
                brand = html.split('Brand: <span class="product-text">')[1].split('</span>')[0].strip()
                
                # Extract category
                category = html.split('Category: <span class="product-text">')[1].split('</span>')[0].strip()
                
                # Extract image URL
                image_url = html.split('id="largeProductImage">')[1].split('src="')[1].split('"')[0].strip()
                
                # Extract store information
                stores = []
                store_sections = html.split('<span class="store-name">')[1:]
                for section in store_sections:
                    try:
                        store_name = section.split('</span>')[0].strip().replace(':', '')
                        price = section.split('<span class="store-link">')[1].split('</span>')[0].strip()
                        link = section.split('href="')[1].split('"')[0].strip()
                        
                        # Convert price to float
                        price_value = float(price.replace('CA$', '').strip())
                        
                        stores.append({
                            'store_name': store_name,
                            'price': price_value,
                            'currency': 'CAD',
                            'link': link,
                            'last_update': datetime.now().isoformat(),
                            'availability': 'In Stock',
                            'shipping': 'See store for details'
                        })
                    except Exception as e:
                        print(f"Error parsing store information: {e}")
                        continue
                
                # Extract attributes
                attributes = {}
                if '<div class="product-text-label">Attributes:' in html:
                    attr_section = html.split('<div class="product-text-label">Attributes:')[1].split('</div>')[0]
                    attr_items = attr_section.split('<li class="product-text"><span>')[1:]
                    for item in attr_items:
                        try:
                            attr = item.split('</span>')[0].strip()
                            key, value = attr.split(': ')
                            attributes[key.lower()] = value
                        except:
                            continue
                
                return {
                    'name': title,
                    'description': description,
                    'brand': brand,
                    'manufacturer': manufacturer,
                    'category': category,
                    'image_url': image_url,
                    'upc': barcode,
                    'mpn': attributes.get('mpn', ''),
                    'size': attributes.get('size', ''),
                    'weight': attributes.get('weight', ''),
                    'color': attributes.get('color', ''),
                    'all_stores': stores,
                    'source': 'Barcode Lookup'
                }
                
            except Exception as e:
                print(f"Error parsing Barcode Lookup HTML: {e}")
                return None
                
    except Exception as e:
        print(f"Error fetching from Barcode Lookup: {str(e)}")
    return None

async def fetch_all_product_data(barcode):
    """Fetch product data from all available sources"""
    async with aiohttp.ClientSession() as session:
        tasks = [
            get_product_from_open_food_facts(session, barcode),
            get_product_from_upc_database(session, barcode),
            get_product_from_barcode_spider(session, barcode),
            get_product_from_price_api(session, barcode),
            get_product_from_google_shopping(session, barcode),
            get_product_from_barcode_lookup(session, barcode)
        ]
        
        print(f"\nFetching data for barcode: {barcode}")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Combine all results
        combined_data = {}
        sources = []
        all_stores = []
        errors = []
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                error_source = tasks[i].__name__.replace('get_product_from_', '')
                print(f"Error from {error_source}: {str(result)}")
                errors.append({
                    'source': error_source,
                    'error': str(result)
                })
                continue
                
            if result:
                source = (result.get('source') or 
                         result.get('source_upc') or 
                         result.get('source_spider') or 
                         result.get('source_google') or
                         result.get('source_barcode_lookup'))
                         
                print(f"Got data from source: {source}")
                
                # Track which sources provided data
                if source:
                    sources.append(source)
                
                # Add store information
                if 'store' in result:
                    all_stores.append(result['store'])
                
                # Add stores from APIs
                if 'all_stores' in result and result['all_stores']:
                    for store in result['all_stores']:
                        if store not in all_stores:  # Avoid duplicates
                            all_stores.append(store)
                
                # Update other fields
                for key, value in result.items():
                    if key not in ['store', 'all_stores'] and value:
                        if key not in combined_data or not combined_data[key]:
                            combined_data[key] = value
        
        if combined_data:
            # Sort stores by price
            all_stores.sort(key=lambda x: float(x['price']) if x.get('price') else float('inf'))
            combined_data['all_stores'] = all_stores
            combined_data['data_sources'] = sources
            combined_data['errors'] = errors if errors else None
            
            # Set the lowest price as the main price
            if all_stores:
                combined_data['price'] = all_stores[0]['price']
                combined_data['currency'] = all_stores[0]['currency']
            
            print(f"Final combined data: {json.dumps(combined_data, indent=2)}")
            return combined_data
        
        if errors:
            print(f"All sources failed: {json.dumps(errors, indent=2)}")
        return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/lookup/<barcode>', methods=['GET'])
def lookup_barcode(barcode):
    """Lookup barcode information"""
    try:
        if not check_rate_limit():
            return jsonify({
                'error': 'Rate limit exceeded',
                'message': 'Please wait before making another request'
            }), 429
        
        request_timestamps.append(datetime.now())
        
        # Validate barcode format
        if not barcode.isdigit():
            return jsonify({
                'error': 'Invalid barcode',
                'message': 'Barcode must contain only numbers'
            }), 400
            
        # Run the async function in the event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        product_data = loop.run_until_complete(fetch_all_product_data(barcode))
        loop.close()
        
        if not product_data:
            return jsonify({
                'error': 'Product not found',
                'message': 'No data found for this barcode',
                'barcode': barcode
            }), 404
            
        # Add request metadata
        product_data['request_time'] = datetime.now().isoformat()
        product_data['barcode'] = barcode
        
        return jsonify(product_data)
        
    except Exception as e:
        print(f"Error processing barcode {barcode}: {str(e)}")
        return jsonify({
            'error': 'Server error',
            'message': str(e),
            'barcode': barcode
        }), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8000) 