import os
import random
from flask import Flask, request, jsonify
from youtube_transcript_api import YouTubeTranscriptApi
import re
import json
from flask_cors import CORS
from openai import OpenAI
import requests

app = Flask(__name__)
CORS(app)

# Environment setup
openai_api_key = os.environ.get("OPENAI_API_KEY")
if not openai_api_key:
    raise ValueError("OPENAI_API_KEY environment variable not set!")

client = OpenAI(api_key=openai_api_key)

# WebShare proxy configuration from your dashboard
WEBSHARE_PROXIES = [
    {"ip": "23.95.150.145", "port": 6114, "username": "dofmcoom", "password": "k8gjbcts7ekn"},
    {"ip": "198.23.239.134", "port": 6540, "username": "dofmcoom", "password": "k8gjbcts7ekn"},
    {"ip": "45.38.107.97", "port": 6014, "username": "dofmcoom", "password": "k8gjbcts7ekn"},
    {"ip": "107.172.163.27", "port": 6543, "username": "dofmcoom", "password": "k8gjbcts7ekn"},
    {"ip": "64.137.96.74", "port": 6641, "username": "dofmcoom", "password": "k8gjbcts7ekn"},
    {"ip": "45.43.186.39", "port": 6257, "username": "dofmcoom", "password": "k8gjbcts7ekn"},
]

def get_random_proxy():
    """Get a random working proxy from WebShare"""
    proxy = random.choice(WEBSHARE_PROXIES)
    proxy_url = f"http://{proxy['username']}:{proxy['password']}@{proxy['ip']}:{proxy['port']}"
    
    return {
        'http': proxy_url,
        'https': proxy_url
    }

def get_video_id(url):
    patterns = [
        r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})",
        r"(?:embed/)([a-zA-Z0-9_-]{11})",
        r"(?:watch\?v=)([a-zA-Z0-9_-]{11})"
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def chunk_text(text, max_chunk_size=3000):
    words = text.split()
    chunks = []
    current_chunk = []
    current_size = 0

    for word in words:
        if current_size + len(word) > max_chunk_size and current_chunk:
            chunks.append(" ".join(current_chunk))
            current_chunk = [word]
            current_size = len(word)
        else:
            current_chunk.append(word)
            current_size += len(word) + 1

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks

def get_transcript_with_webshare_proxy(video_id, max_retries=3):
    """
    Fetch transcript using WebShare proxies with fallback
    """
    
    for attempt in range(max_retries):
        try:
            print(f"Attempt {attempt + 1}: Trying with WebShare proxy")
            
            # Get a random proxy
            proxy_dict = get_random_proxy()
            print(f"Using proxy: {proxy_dict['http'].split('@')[1]}")  # Log proxy IP (without credentials)
            
            # Method 1: Direct proxy approach using requests
            try:
                # Build the transcript URL manually
                transcript_url = f"https://www.youtube.com/api/timedtext?v={video_id}&lang=en&fmt=json3"
                
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
                }
                
                response = requests.get(
                    transcript_url, 
                    headers=headers, 
                    proxies=proxy_dict, 
                    timeout=30
                )
                
                if response.status_code == 200:
                    content = response.text
                    if content and len(content) > 100:
                        # Parse YouTube's JSON3 format
                        transcript_data = json.loads(content)
                        if 'events' in transcript_data:
                            transcript = []
                            for event in transcript_data['events']:
                                if 'segs' in event:
                                    text_parts = []
                                    for seg in event['segs']:
                                        if 'utf8' in seg:
                                            text_parts.append(seg['utf8'])
                                    if text_parts:
                                        transcript.append({
                                            'text': ''.join(text_parts),
                                            'start': event.get('tStartMs', 0) / 1000.0,
                                            'duration': event.get('dDurationMs', 0) / 1000.0
                                        })
                            
                            if transcript:
                                print(f"Successfully fetched transcript via direct API with proxy")
                                return transcript
                
            except Exception as e:
                print(f"Direct API method failed: {str(e)}")
            
            # Method 2: YouTube Transcript API with proxy (if available)
            try:
                # Note: youtube-transcript-api doesn't directly support requests-style proxies
                # This is a workaround by monkey-patching
                import youtube_transcript_api._api
                original_get = requests.Session.get
                
                def proxied_get(self, *args, **kwargs):
                    kwargs['proxies'] = proxy_dict
                    return original_get(self, *args, **kwargs)
                
                # Temporarily patch the requests
                requests.Session.get = proxied_get
                
                transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=['en', 'en-US'])
                
                # Restore original method
                requests.Session.get = original_get
                
                print(f"Successfully fetched transcript via YouTube Transcript API with proxy")
                return transcript
                
            except Exception as e:
                print(f"YouTube Transcript API with proxy failed: {str(e)}")
                # Restore original method just in case
                requests.Session.get = original_get
                
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {str(e)}")
            if attempt < max_retries - 1:
                import time
                time.sleep(2)  # Wait before retry
                
    # Final fallback: Try without proxy
    try:
        print("Trying final fallback without proxy")
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=['en', 'en-US'])
        print("Fallback without proxy succeeded")
        return transcript
    except Exception as e:
        raise Exception(f"All methods failed including non-proxy fallback. Last error: {str(e)}")

@app.route('/generate_qa', methods=['POST'])
def generate_qa():
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        url = data.get('url', '').strip()
        count = int(data.get('count', 10))

        if not url:
            return jsonify({'error': 'YouTube URL is required'}), 400

        if count < 1 or count > 50:
            return jsonify({'error': 'Question count must be between 1 and 50'}), 400

        video_id = get_video_id(url)
        if not video_id:
            return jsonify({'error': 'Invalid YouTube URL format'}), 400

        print(f"Processing video ID: {video_id}")

        # Get transcript with WebShare proxy
        try:
            transcript = get_transcript_with_webshare_proxy(video_id)
            print(f"Successfully fetched transcript with {len(transcript)} entries")
            
        except Exception as e:
            print(f"Transcript fetching failed: {str(e)}")
            return jsonify({
                'error': f"Could not fetch transcript: {str(e)}",
                'suggestion': 'Video might not have captions, or all proxy methods failed. Try a different video.',
                'video_id': video_id,
                'proxy_status': 'WebShare proxies attempted'
            }), 500

        # Process transcript
        full_text = " ".join([entry.get('text', '') for entry in transcript])

        if len(full_text.strip()) < 100:
            return jsonify({'error': 'Transcript too short to generate meaningful questions'}), 400

        chunks = chunk_text(full_text)
        text_to_process = chunks[0]

        print(f"Processing text chunk of length: {len(text_to_process)}")

        # Enhanced prompt for better Q&A generation
        prompt = f"""Based on the following video transcript, generate exactly {count} educational question-answer pairs in JSON format.

Requirements:
- Return ONLY a valid JSON array, no other text
- Each question should be educational and test understanding
- Each answer should be comprehensive but concise (2-4 sentences)
- Focus on main concepts, key points, and important details
- Avoid yes/no questions - make them descriptive
- Questions should encourage learning and comprehension

Strict Format (return only this JSON structure):
[
  {{"question": "What is the main concept explained about...?", "answer": "The main concept is... It works by... This is important because..."}},
  {{"question": "How does the speaker explain...?", "answer": "According to the transcript, the process involves... The key steps are..."}}
]

Video Transcript:
{text_to_process}"""

        # Call OpenAI API
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are an educational content generator. Always return valid JSON arrays only, without any additional text or formatting."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=2500,
                temperature=0.7
            )

            result = response.choices[0].message.content.strip()
            result = result.replace('```json', '').replace('```', '').strip()

            json_result = json.loads(result)
            
            if not isinstance(json_result, list) or len(json_result) == 0:
                return jsonify({'error': 'AI returned invalid response format'}), 500

            print(f"Successfully generated {len(json_result)} Q&A pairs")
            
            return jsonify({
                'result': result, 
                'count': len(json_result),
                'video_id': video_id,
                'transcript_length': len(full_text),
                'proxy_method': 'WebShare proxies'
            })

        except json.JSONDecodeError as e:
            return jsonify({'error': 'AI returned invalid JSON format', 'details': str(e)}), 500
        except Exception as e:
            return jsonify({'error': f'OpenAI API error: {str(e)}'}), 500

    except Exception as e:
        print(f"Server error: {str(e)}")
        return jsonify({'error': f"Server error: {str(e)}"}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'Server is running',
        'openai_configured': bool(openai_api_key),
        'proxy_configured': True,
        'proxy_count': len(WEBSHARE_PROXIES),
        'proxy_provider': 'WebShare'
    })

@app.route('/test_proxy', methods=['GET'])
def test_proxy():
    """Test WebShare proxy connection"""
    try:
        proxy_dict = get_random_proxy()
        
        # Test the proxy with a simple request
        response = requests.get(
            'https://httpbin.org/ip', 
            proxies=proxy_dict, 
            timeout=10
        )
        
        if response.status_code == 200:
            ip_info = response.json()
            return jsonify({
                'success': True,
                'proxy_ip': ip_info.get('origin'),
                'message': 'WebShare proxy is working!',
                'proxy_used': proxy_dict['http'].split('@')[1]  # Show IP without credentials
            })
        else:
            return jsonify({
                'success': False,
                'error': f'Proxy returned status code: {response.status_code}'
            }), 500
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Proxy test failed: {str(e)}'
        }), 500

@app.route('/test_transcript/<video_id>', methods=['GET'])
def test_transcript(video_id):
    """Test transcript fetching with WebShare proxy"""
    try:
        transcript = get_transcript_with_webshare_proxy(video_id)
        full_text = " ".join([entry.get('text', '') for entry in transcript])
        return jsonify({
            'success': True,
            'video_id': video_id,
            'transcript_length': len(full_text),
            'preview': full_text[:300] + "..." if len(full_text) > 300 else full_text,
            'method_used': 'WebShare proxies'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'video_id': video_id,
            'error': str(e)
        }), 500

if __name__ == '__main__':
    if not openai_api_key:
        print("⚠️ Warning: OPENAI_API_KEY not set!")
    else:
        print("✅ OpenAI API key configured")
    
    print(f"✅ WebShare proxies configured: {len(WEBSHARE_PROXIES)} proxies available")
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)