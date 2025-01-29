# Barcode Lookup Application

A web application that looks up product information using multiple barcode databases and displays comprehensive product details including prices in CAD.

## Features

- Multi-source product lookup (Open Food Facts, UPC Database, Barcode Spider)
- Automatic currency conversion to CAD
- Detailed product information display including:
  - Product images
  - Pricing from multiple stores
  - Nutritional information (when available)
  - Product specifications
  - Store availability
- Modern, responsive UI
- Real-time barcode lookup

## Technologies Used

- Backend:
  - Python 3.x
  - Flask
  - aiohttp for async API calls
  - requests for currency conversion

- Frontend:
  - HTML5
  - TailwindCSS
  - Font Awesome icons
  - Vanilla JavaScript

## Setup

1. Clone the repository:
```bash
git clone https://github.com/yourusername/barcode-lookup.git
cd barcode-lookup
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Create a `.env` file in the project root with your API keys:
```
BARCODE_API_KEY=your_barcode_api_key
UPC_DATABASE_API_KEY=your_upc_database_api_key
BARCODE_SPIDER_API_KEY=your_barcode_spider_api_key
EXCHANGE_RATE_API_KEY=your_exchange_rate_api_key
```

4. Run the application:
```bash
python app.py
```

5. Open your browser and navigate to `http://localhost:5000`

## API Sources

- [Open Food Facts](https://world.openfoodfacts.org/data) - Open database of food products
- [UPC Database](https://upcdatabase.org/api) - General product database
- [Barcode Spider](https://barcodespider.com) - Detailed product information and store prices

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details. 