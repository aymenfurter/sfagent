# Salesforce Assistant with Azure AI

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![Azure](https://img.shields.io/badge/Azure-Integrated-blue)

This application integrates Salesforce with Azure AI to provide a conversational assistant capable of retrieving and managing Salesforce data. The assistant uses OpenTelemetry for tracing and supports Bing grounding for general queries.

## Features
- **Fetch Salesforce accounts and contacts** using custom functions.
- **Integrate with Azure AI Projects** for conversational capabilities.
- **Optional Bing grounding** for web-based queries.
- **OpenTelemetry tracing** for monitoring and debugging.
- **Gradio-based user interface** for interaction.

## Setup

### Run the Application
1. **Start the application**:
   ```bash
   python main.py
   ```

2. **Access the Gradio interface** at the URL provided in the terminal.

## Testing
Run automated tests using:
```bash
python execute_automated_tests.py
```

Test results will be saved in the `test_results` directory.