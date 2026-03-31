#!/usr/bin/env python3
"""Test Squiggle API to debug historical data issues."""
import asyncio
import httpx
import json

async def test_squiggle_api():
    """Test Squiggle API for different years."""
    base_url = "https://api.squiggle.com.au"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Test different years
        years = [2010, 2015, 2020, 2024, 2025, 2026]
        
        for year in years:
            # Test WITH complete=true
            url = f"{base_url}/?q=games;year={year};complete=true"
            print(f"\n{'='*60}")
            print(f"Testing year {year} WITH complete=true")
            print(f"URL: {url}")
            
            try:
                response = await client.get(
                    url,
                    headers={"User-Agent": "WhatIsMyTip - contact@whatismytip.com"}
                )
                print(f"Status: {response.status_code}")
                
                if response.status_code == 200:
                    data = response.json()
                    games = data.get("games", [])
                    print(f"Games count: {len(games)}")
                    
                    if games:
                        # Show first game keys
                        print(f"First game keys: {list(games[0].keys())}")
                        print(f"First game date: {games[0].get('date')}")
                else:
                    print(f"Error: {response.text}")
                    
            except Exception as e:
                print(f"Exception: {e}")
        
        # Test without complete parameter
        print(f"\n{'='*60}")
        print("Testing year 2024 WITHOUT complete parameter")
        url = f"{base_url}/?q=games;year=2024"
        print(f"URL: {url}")
        
        try:
            response = await client.get(
                url,
                headers={"User-Agent": "WhatIsMyTip - contact@whatismytip.com"}
            )
            print(f"Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                games = data.get("games", [])
                print(f"Games count: {len(games)}")
                
                if games:
                    # Show first game keys
                    print(f"First game keys: {list(games[0].keys())}")
                    print(f"First game date: {games[0].get('date')}")
            else:
                print(f"Error: {response.text}")
                
        except Exception as e:
            print(f"Exception: {e}")

if __name__ == "__main__":
    asyncio.run(test_squiggle_api())
