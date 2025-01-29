from flask import Flask, render_template, request, jsonify
import requests
import os
import time
from dotenv import load_dotenv
import json
from datetime import datetime, timedelta
from collections import deque
import asyncio
import aiohttp

load_dotenv()

app = Flask(__name__)

# API Configuration
BARCODE_API_KEY = os.getenv('BARCODE_API_KEY')
BARCODE_API_URL = 'https://barcodereport.com/api'
OPEN_FOOD_FACTS_API = 'https://world.openfoodfacts.org/api/v0'
UPC_DATABASE_API_KEY = os.getenv('UPC_DATABASE_API_KEY')
UPC_DATABASE_API = 'https://api.upcdatabase.org/product'
BARCODE_SPIDER_API_KEY = os.getenv('BARCODE_SPIDER_API_KEY')
BARCODE_SPIDER_API = 'https://api.barcodespider.com/v1/lookup'

# Exchange rate API (using exchangerate-api.com)
EXCHANGE_RATE_API = 'https://v6.exchangerate-api.com/v6/YOUR_API_KEY/latest/USD'
exchange_rates_cache = {
    'rates': {},
    'last_updated': None
}

# Rate limiting setup
RATE_LIMIT_PERIOD = 60
MAX_REQUESTS = 30
request_timestamps = deque(maxlen=MAX_REQUESTS)

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
        headers = {
            'Authorization': f'Bearer {UPC_DATABASE_API_KEY}'
        }
        url = f"{UPC_DATABASE_API}/v1/product/{barcode}"
        
        print(f"Fetching from UPC Database: {url}")
        async with session.get(url, headers=headers) as response:
            response_text = await response.text()
            print(f"UPC Database raw response: {response_text}")
            
            if response.status == 200:
                try:
                    data = json.loads(response_text)
                    print(f"UPC Database parsed response: {data}")
                    
                    if 'success' in data and not data['success']:
                        print(f"UPC Database error: {data.get('message', 'Unknown error')}")
                        return None
                    
                    price = data.get('price')
                    if price:
                        price_cad = convert_price_to_cad(price, data.get('currency', 'USD'))
                    else:
                        price_cad = None

                    return {
                        'name': data.get('title'),
                        'description': data.get('description'),
                        'brand': data.get('brand'),
                        'manufacturer': data.get('manufacturer'),
                        'category': data.get('category'),
                        'price': price_cad,
                        'price_original': {
                            'amount': price,
                            'currency': data.get('currency', 'USD')
                        },
                        'msrp': convert_price_to_cad(data.get('msrp'), data.get('currency', 'USD')),
                        'currency': 'CAD',
                        'image_url': data.get('image'),
                        'upc': data.get('upc'),
                        'ean': data.get('ean'),
                        'model': data.get('model'),
                        'color': data.get('color'),
                        'size': data.get('size'),
                        'dimension': data.get('dimension'),
                        'weight': data.get('weight'),
                        'last_update': data.get('last_update'),
                        'source_upc': 'UPC Database'
                    }
                except json.JSONDecodeError as e:
                    print(f"Failed to parse UPC Database response: {e}")
                    return None
    except Exception as e:
        print(f"Error fetching from UPC Database: {str(e)}")
    return None

async def get_product_from_barcode_spider(session, barcode):
    """Get product information from Barcode Spider"""
    try:
        headers = {
            'token': BARCODE_SPIDER_API_KEY,
            'Host': 'api.barcodespider.com',
            'Accept': 'application/json'
        }
        url = f"{BARCODE_SPIDER_API}?upc={barcode}"
        
        print(f"Fetching from Barcode Spider: {url}")
        async with session.get(url, headers=headers) as response:
            response_text = await response.text()
            print(f"Barcode Spider raw response: {response_text}")
            
            if response.status == 200:
                try:
                    data = json.loads(response_text)
                    print(f"Barcode Spider parsed response: {data}")
                    
                    if data.get('item_response', {}).get('code') == 200:
                        item = data.get('item_attributes', {})
                        stores = data.get('Stores', [])
                        
                        # Get all store prices and details
                        store_details = []
                        lowest_price_cad = float('inf')
                        
                        for store in stores:
                            if store.get('price'):
                                try:
                                    price = float(store['price'])
                                    price_cad = convert_price_to_cad(price, store.get('currency', 'USD'))
                                    
                                    if price_cad and price_cad < lowest_price_cad:
                                        lowest_price_cad = price_cad
                                    
                                    store_details.append({
                                        'store_name': store.get('store_name'),
                                        'price': price_cad,
                                        'price_original': {
                                            'amount': price,
                                            'currency': store.get('currency', 'USD')
                                        },
                                        'currency': 'CAD',
                                        'link': store.get('link'),
                                        'last_update': store.get('updated')
                                    })
                                except (ValueError, TypeError) as e:
                                    print(f"Error parsing store price: {e}")
                                    continue
                        
                        if lowest_price_cad == float('inf'):
                            lowest_price_cad = None
                        
                        return {
                            'name': item.get('title'),
                            'description': item.get('description'),
                            'brand': item.get('brand'),
                            'manufacturer': item.get('manufacturer'),
                            'publisher': item.get('publisher'),
                            'model': item.get('model'),
                            'mpn': item.get('mpn'),
                            'upc': item.get('upc'),
                            'ean': item.get('ean'),
                            'parent_category': item.get('parent_category'),
                            'category': item.get('category'),
                            'color': item.get('color'),
                            'size': item.get('size'),
                            'weight': item.get('weight'),
                            'image_url': item.get('image'),
                            'price': lowest_price_cad,
                            'currency': 'CAD',
                            'all_stores': store_details,
                            'asin': item.get('asin'),
                            'source_spider': 'Barcode Spider'
                        }
                    else:
                        print(f"Barcode Spider error response: {data.get('item_response', {}).get('message', 'Unknown error')}")
                except json.JSONDecodeError as e:
                    print(f"Failed to parse Barcode Spider response: {e}")
                    return None
    except Exception as e:
        print(f"Error fetching from Barcode Spider: {str(e)}")
    return None

async def fetch_all_product_data(barcode):
    """Fetch product data from all available sources"""
    async with aiohttp.ClientSession() as session:
        tasks = [
            get_product_from_open_food_facts(session, barcode),
            get_product_from_upc_database(session, barcode),
            get_product_from_barcode_spider(session, barcode)
        ]
        results = await asyncio.gather(*tasks)
        
        # Combine all results
        combined_data = {}
        sources = []
        
        for result in results:
            if result:
                print(f"Processing result from source: {result.get('source') or result.get('source_upc') or result.get('source_spider')}")
                # Track which sources provided data
                if 'source' in result:
                    sources.append(result['source'])
                if 'source_upc' in result:
                    sources.append(result['source_upc'])
                if 'source_spider' in result:
                    sources.append(result['source_spider'])
                
                # Special handling for prices
                if 'price' in result and result['price']:
                    if 'price' not in combined_data or combined_data['price'] is None:
                        combined_data['price'] = result['price']
                    else:
                        try:
                            if float(result['price']) < float(combined_data['price']):
                                combined_data['price'] = result['price']
                        except (ValueError, TypeError):
                            pass
                
                # Special handling for stores
                if 'all_stores' in result and result['all_stores']:
                    if 'all_stores' not in combined_data:
                        combined_data['all_stores'] = []
                    combined_data['all_stores'].extend(result['all_stores'])
                
                # Update other fields
                for key, value in result.items():
                    if key not in ['price', 'all_stores'] and value:
                        if key not in combined_data or not combined_data[key]:
                            combined_data[key] = value
        
        if combined_data:
            combined_data['data_sources'] = sources
            print(f"Final combined data: {json.dumps(combined_data, indent=2)}")
            return combined_data
        
        return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/lookup', methods=['POST'])
def lookup_barcode():
    if not check_rate_limit():
        return jsonify({
            'error': 'Rate limit exceeded',
            'message': 'Please wait a moment before trying again'
        }), 429

    barcode = request.form.get('barcode')
    
    if not barcode:
        return jsonify({'error': 'Barcode is required'}), 400

    try:
        # Run the async function to fetch data from all sources
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        combined_data = loop.run_until_complete(fetch_all_product_data(barcode))
        loop.close()

        if combined_data:
            return jsonify(combined_data)

        # If all APIs fail to find the product
        return jsonify({
            'error': 'Barcode not found',
            'message': 'Product not found in any database'
        }), 404

    except Exception as e:
        print(f"Unexpected error occurred: {str(e)}")
        return jsonify({
            'error': 'Server error',
            'message': str(e)
        }), 500

if __name__ == '__main__':
    app.run(debug=True) 