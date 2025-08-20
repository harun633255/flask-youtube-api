from flask import Flask, request, jsonify
from youtube_transcript_api import YouTubeTranscriptApi
import re
import json
from flask_cors import CORS
from openai import OpenAI
import requests
import time
import random

app = Flask(__name__)
CORS(app)

# Updated for Azure deployment
# ✅ Get OpenAI API key from environment variables (Render environment)
import os
openai_api_key = os.environ.get("OPENAI_API_KEY")

if not openai_api_key:
    raise ValueError("OPENAI_API_KEY environment variable not set!")

# Initialize OpenAI client
client = OpenAI(api_key=openai_api_key)

# Function to extract YouTube video ID
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

def get_transcript_with_retry(video_id, max_retries=3):
    """
    Enhanced transcript fetching with multiple retry strategies
    """
    
    # Different user agents to rotate
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0'
    ]
    
    # Try different language combinations
    language_combinations = [
        ['en'],
        ['en-US'],
        ['en', 'en-US'],
        ['en', 'bn', 'hi'],
        None  # Let YouTube decide
    ]
    
    for attempt in range(max_retries):
        try:
            print(f"Attempt {attempt + 1} to fetch transcript for video {video_id}")
            
            # Add random delay to avoid rate limiting
            if attempt > 0:
                delay = random.uniform(2, 5)
                print(f"Waiting {delay:.2f} seconds before retry...")
                time.sleep(delay)
            
            # Try different language combinations
            for lang_combo in language_combinations:
                try:
                    if lang_combo:
                        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=lang_combo)
                    else:
                        transcript = YouTubeTranscriptApi.get_transcript(video_id)
                    
                    print(f"Successfully fetched transcript with languages: {lang_combo}")
                    return transcript
                    
                except Exception as lang_error:
                    print(f"Language combination {lang_combo} failed: {str(lang_error)}")
                    continue
            
            # If all language combinations failed, try the list method
            try:
                transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                for transcript_obj in transcript_list:
                    try:
                        transcript = transcript_obj.fetch()
                        print("Successfully fetched transcript using list method")
                        return transcript
                    except:
                        continue
            except Exception as list_error:
                print(f"List method failed: {str(list_error)}")
                
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {str(e)}")
            if attempt == max_retries - 1:
                raise Exception(f"All {max_retries} attempts failed. Last error: {str(e)}")
    
    raise Exception("Failed to fetch transcript after all attempts")

# Split long transcript into chunks
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

        # Get transcript with enhanced retry logic
        try:
            transcript = get_transcript_with_retry(video_id)
        except Exception as e:
            print(f"Transcript fetching failed: {str(e)}")
            return jsonify({
                'error': f"Could not fetch transcript: {str(e)}",
                'suggestion': 'This video might not have captions available, or YouTube is blocking the request. Try a different video with captions enabled.'
            }), 500

        # Process transcript
        if hasattr(transcript[0], 'text'):
            full_text = " ".join([entry.text for entry in transcript])
        else:
            full_text = " ".join([entry.get('text', '') for entry in transcript])

        if len(full_text.strip()) < 100:
            return jsonify({'error': 'Transcript too short to generate meaningful questions'}), 400

        chunks = chunk_text(full_text)
        text_to_process = chunks[0]  # Use first chunk for processing

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

        # Call OpenAI API with error handling
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
            print(f"OpenAI response length: {len(result)}")

            # Clean the result to ensure it's valid JSON
            result = result.replace('```json', '').replace('```', '').strip()

            # Validate JSON
            json_result = json.loads(result)
            
            if not isinstance(json_result, list):
                return jsonify({'error': 'AI returned invalid response format'}), 500
            
            if len(json_result) == 0:
                return jsonify({'error': 'No questions generated'}), 500

            print(f"Successfully generated {len(json_result)} Q&A pairs")
            
            return jsonify({
                'result': result, 
                'count': len(json_result),
                'video_id': video_id,
                'transcript_length': len(full_text)
            })

        except json.JSONDecodeError as e:
            print(f"JSON parsing error: {str(e)}")
            return jsonify({'error': 'AI returned invalid JSON format', 'details': str(e)}), 500
        
        except Exception as e:
            print(f"OpenAI API error: {str(e)}")
            return jsonify({'error': f'OpenAI API error: {str(e)}'}), 500

    except Exception as e:
        print(f"Server error: {str(e)}")
        return jsonify({'error': f"Server error: {str(e)}"}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'Server is running',
        'openai_configured': bool(openai_api_key),
        'environment': 'production' if os.environ.get('RENDER') else 'development'
    })

@app.route('/test_transcript/<video_id>', methods=['GET'])
def test_transcript(video_id):
    """Test endpoint to check if transcript fetching works for a specific video"""
    try:
        transcript = get_transcript_with_retry(video_id)
        full_text = " ".join([entry.get('text', '') for entry in transcript])
        return jsonify({
            'success': True,
            'video_id': video_id,
            'transcript_length': len(full_text),
            'preview': full_text[:200] + "..." if len(full_text) > 200 else full_text
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
    
    # Use environment port or default to 5000
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)  # Set debug=False for production# Updated for deployment
