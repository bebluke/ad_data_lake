import os
from dotenv import load_dotenv
from facebook_business.api import FacebookAdsApi

def get_api_client():
    """
    從 .env 檔案讀取 Access Token 並初始化 FacebookAdsApi 客戶端
    """
    load_dotenv(encoding='utf-8-sig')
    access_token = os.getenv("META_ACCESS_TOKEN")
    if not access_token or access_token == "<請在此貼上您的長期有效用戶存取權杖>":
        raise ValueError("請在 .env 檔案中設定您的 META_ACCESS_TOKEN")
    
    # 在此處可以加入 App ID 和 App Secret，但對於用戶權杖認證非必需
    api = FacebookAdsApi.init(access_token=access_token)
    return api
