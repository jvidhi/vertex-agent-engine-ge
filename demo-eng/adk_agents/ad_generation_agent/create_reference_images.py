import os
import argparse
import datetime
import random
import string
import mimetypes
import tomllib
from google import genai
from google.genai import types
from google.cloud import storage

# Utility to load .env manually if python-dotenv is not active in this shell
def load_env():
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        value = value.strip('"').strip("'")
                        os.environ[key] = value

load_env()

# Retrieve values from environment
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
BUCKET_NAME = os.environ.get("GOOGLE_CLOUD_BUCKET_ARTIFACTS")
LOCATION = os.environ.get("MODELS_CLOUD_LOCATION", "global")
MODEL_NAME = os.environ.get("IMAGE_GENERATION_MODEL", "gemini-3.1-flash-image-preview")
TEXT_MODEL_NAME = os.environ.get("LLM_GEMINI_MODEL_ADGEN_ROOT", "gemini-3.1-pro-preview")
DEMO_COMPANY_NAME = os.environ.get("DEMO_COMPANY_NAME", "reference_images")

# Force override of GOOGLE_CLOUD_LOCATION to ensure SDK uses the intended model location
os.environ["GOOGLE_CLOUD_LOCATION"] = LOCATION

def load_brand_config(company_name):
    """Loads the brand TOML configuration."""
    brand_file = f"brand_configs/{company_name.lower()}.toml"
    if os.path.exists(brand_file):
        with open(brand_file, "rb") as f:
            return tomllib.load(f)
    print(f"⚠️ Brand config {brand_file} not found.")
    return None

def generate_asset_prompt(client, brand_info, asset_type="character"):
    """Uses Gemini to generate an excellent image generation prompt based on brand info."""
    brand_name = brand_info.get("brand_name", DEMO_COMPANY_NAME)
    brand_guidelines = brand_info.get("brand_guidelines", "")
    
    if asset_type == "logo":
        instruction = f"Generate a high-end, professional image generation prompt for a brand logo for '{brand_name}'. The logo should align with these brand guidelines: {brand_guidelines}. The prompt should describe the logo in detail, including colors, symbols, and style, suitable for a photorealistic image generator. Focus on a clean, isolated logo on a neutral background."
    else:
        instruction = f"Generate a high-end, professional image generation prompt for a main character for '{brand_name}'. The character should embody the brand identity described here: {brand_guidelines}. Describe the character's appearance, clothing, and vibe in detail. The prompt should be suitable for a photorealistic image generator."

    print(f"🧠 Generating excellent {asset_type} prompt for {brand_name}...")
    
    response = client.models.generate_content(
        model=TEXT_MODEL_NAME,
        contents=[instruction],
        config=types.GenerateContentConfig(
            temperature=0.7,
            top_p=0.95,
            top_k=40,
            max_output_tokens=300,
        )
    )
    
    if response and response.text:
        return response.text.strip()
    return f"A professional {asset_type} for {brand_name}"

def upload_to_gcs(bucket_name, file_bytes, destination_blob_name, mime_type="image/png"):
    """Uploads file bytes to Google Cloud Storage."""
    if not bucket_name:
        raise ValueError("GCS Bucket Name is not set. Check your .env file.")
        
    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)
    blob.upload_from_string(file_bytes, content_type=mime_type)
    
    gs_uri = f"gs://{bucket_name}/{destination_blob_name}"
    # Also generate the authenticated URL style that the agent uses
    auth_url = f"https://storage.cloud.google.com/{bucket_name}/{destination_blob_name}"
    
    print(f"\n✅ Uploaded successfully:")
    print(f"   GCS URI: {gs_uri}")
    print(f"   Auth URL: {auth_url}")
    return gs_uri

def upload_local_directory_to_gcs(local_path, bucket_name, gcs_prefix):
    """Uploads a local directory to Google Cloud Storage."""
    if not bucket_name:
        raise ValueError("GCS Bucket Name is not set. Check your .env file.")
        
    if not os.path.isdir(local_path):
        print(f"⚠️ Local directory {local_path} not found. Skipping upload.")
        return

    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(bucket_name)

    print(f"⬆️ Uploading folder '{local_path}' to gs://{bucket_name}/{gcs_prefix}/")
    
    for root, dirs, files in os.walk(local_path):
        for file in files:
            local_file_path = os.path.join(root, file)
            # Calculate the relative path from local_path to maintain structure
            relative_path = os.path.relpath(local_file_path, local_path)
            # Construct the destination blob name (ensure forward slashes for GCS)
            destination_blob_name = f"{gcs_prefix}/{relative_path}".replace("\\", "/")
            
            blob = bucket.blob(destination_blob_name)
            blob.upload_from_filename(local_file_path)
            print(f"   ✅ {relative_path} -> {destination_blob_name}")

def generate_reference_image(client, prompt, aspect_ratio="9:16"):
    """Generates an image using the google-genai client."""
    if not PROJECT_ID:
        raise ValueError("Google Cloud Project ID is not set. Check your .env file.")

    print(f"🤖 Initializing generation with model: {MODEL_NAME}")
    print(f"📝 Prompt: {prompt}")

    # Heuristic to determine if we should use Imagen API vs Gemini Content API
    if "gemini" in MODEL_NAME.lower():
        print("ℹ️ Using Gemini Text-to-Image content endpoint...")
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
            image_config=types.ImageConfig(aspect_ratio=aspect_ratio)
        )
        
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[prompt],
            config=config
        )
        
        if (
            response
            and response.candidates
            and response.candidates[0]
            and response.candidates[0].content
            and response.candidates[0].content.parts
        ):
            for part in response.candidates[0].content.parts:
                if hasattr(part, "inline_data") and part.inline_data and part.inline_data.data:
                    return part.inline_data.data, "image/png"
        
        raise Exception("Gemini API call succeeded but returned no image payload.")
        
    else:
        print("ℹ️ Using Imagen API...")
        response = client.models.generate_images(
            model=MODEL_NAME,
            prompt=prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio=aspect_ratio,
                output_mime_type="image/png",
            )
        )
        
        if response and response.generated_images:
            for generated_image in response.generated_images:
                if generated_image.image and generated_image.image.image_bytes:
                    mime_type = generated_image.image.mime_type or "image/png"
                    return generated_image.image.image_bytes, mime_type

        raise Exception("Imagen API call succeeded but returned no image payload.")

def run_workflow(company_name, args, client):
    """Executes the asset generation and upload workflow for a specific company."""
    # Resolve GCS folder (prefix) from company_name
    brand_path = company_name.lower().replace(" ", "_").strip("/")
    gcs_folder = f"brand_configs/{brand_path}"
    
    # Generate timestamp and random suffix
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    rand_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))

    # Resolve filename
    base_name = args.filename if args.filename else f"ref_image_{timestamp}_{rand_id}"

    if args.init:
        print(f"\n🚀 Initializing brand assets for: {company_name}")
        brand_info = load_brand_config(company_name)
        if not brand_info:
            return

        # Generate Logo
        logo_prompt = generate_asset_prompt(client, brand_info, asset_type="logo")
        print(f"✨ Logo Prompt: {logo_prompt}")
        try:
            image_bytes, mime_type = generate_reference_image(client, logo_prompt, aspect_ratio="1:1")
            upload_to_gcs(BUCKET_NAME, image_bytes, f"{gcs_folder}/{brand_path}_sample_logo.png", mime_type)
        except Exception as e:
            print(f"❌ Logo generation failed: {e}")

        # Generate Character
        char_prompt = generate_asset_prompt(client, brand_info, asset_type="character")
        print(f"✨ Character Prompt: {char_prompt}")
        try:
            image_bytes, mime_type = generate_reference_image(client, char_prompt, aspect_ratio="9:16")
            upload_to_gcs(BUCKET_NAME, image_bytes, f"{gcs_folder}/{brand_path}_sample_character.png", mime_type)
        except Exception as e:
            print(f"❌ Character generation failed: {e}")
        
        return

    if not args.prompt:
        print(f"❌ Error: --prompt is required for {company_name} unless using --init")
        return

    try:
        # 1. Generate Image
        image_bytes, mime_type = generate_reference_image(client, args.prompt, aspect_ratio=args.aspect_ratio)
        
        # 2. Resolve final filename with extension
        ext = mimetypes.guess_extension(mime_type) or ".png"
        final_filename = f"{base_name}{ext}"
        
        # 3. Construct GCS Path
        destination_blob = f"{gcs_folder}/{final_filename}"

        # 4. Upload
        upload_to_gcs(BUCKET_NAME, image_bytes, destination_blob, mime_type)

    except Exception as e:
        print(f"\n❌ Error for {company_name}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Standalone script to generate a reference image and save it to GCS.")
    parser.add_argument("--prompt", type=str, help="Description of the image to generate.")
    parser.add_argument("--filename", type=str, help="Custom filename (without extension). Defaults to random name.")
    parser.add_argument("--aspect_ratio", type=str, default="9:16", choices=["9:16", "16:9", "1:1", "3:4", "4:3"], help="Aspect ratio of the image.")
    parser.add_argument("--init", action="store_true", help="Initialize brand by generating a sample logo and main character.")
    parser.add_argument("--all", action="store_true", help="Run the workflow for all brands in brand_configs/ (excludes example_brand.toml).")

    args = parser.parse_args()

    # 0. Upload brand configs first (as required by the system design)
    brand_configs_local = "brand_configs"
    brand_configs_gcs_prefix = "brand_configs"
    upload_local_directory_to_gcs(brand_configs_local, BUCKET_NAME, brand_configs_gcs_prefix)
    
    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION
    )

    if args.all:
        print("🌍 Running workflow for all detected brands...")
        brand_files = [f for f in os.listdir(brand_configs_local) if f.endswith(".toml") and f != "example_brand.toml"]
        for brand_file in brand_files:
            company_name = brand_file.replace(".toml", "")
            run_workflow(company_name, args, client)
    else:
        run_workflow(DEMO_COMPANY_NAME, args, client)

if __name__ == "__main__":
    main()
