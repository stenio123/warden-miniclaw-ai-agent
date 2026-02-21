def run(args: dict) -> str:
    import json
    
    # Get the input string
    input_string = args.get('text', '')
    
    # Reverse the string
    reversed_string = input_string[::-1]
    
    # Return structured JSON response
    result = {
        "result": reversed_string,
        "source": "string_reversal_operation"
    }
    
    return json.dumps(result)