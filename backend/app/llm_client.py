import json
import os
import re
import asyncio
from datetime import datetime
from typing import List, Dict, Optional, Callable, Any
from openai import AsyncOpenAI
from .prompts import TASK_ANALYSIS_PROMPT, RELEVANCE_ASSESSMENT_PROMPT, CLICK_DECISION_PROMPT, ANSWER_GENERATION_PROMPT

class LLMClient:
    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1", model: str = "deepseek-ai/deepseek-v3.2"):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def _extract_json(self, text: str) -> Optional[Dict]:
        """Helper to safely extract JSON from LLM response"""
        try:
            # 1. Try to find code block with json
            match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            
            # 2. Try to find code block without language
            match = re.search(r'```\s*(\{.*?\})\s*```', text, re.DOTALL)
            if match:
                return json.loads(match.group(1))
                
            # 3. Try to find raw JSON object
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
                
            return None
        except (json.JSONDecodeError, ValueError, KeyError):
            return None

    async def analyze_task(self, user_input: str, history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """
        [02] AI Model: Task Analysis
        Returns: {"type": "search", "queries": ["query1", "query2"]} or {"type": "direct", "url": "..."}
        """
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        system_prompt = TASK_ANALYSIS_PROMPT.format(current_time=current_time)

        messages = [{"role": "system", "content": system_prompt}]
        
        # Add conversation history if available
        if history:
            # We only take the last few turns to keep context relevant and short
            recent_history = history[-6:] 
            for msg in recent_history:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                messages.append({"role": role, "content": content})
        
        messages.append({"role": "user", "content": user_input})
        
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages
            )
            content = response.choices[0].message.content
            
            data = self._extract_json(content)
            if data:
                return data
            
            # Fallback
            return {"type": "search", "queries": [user_input]}
            
        except Exception as e:
            print(f"Error in analyze_task: {e}")
            return {"type": "search", "queries": [user_input]}

    async def assess_relevance(self, query: str, snippets: List[Dict]) -> List[int]:
        """
        [04] AI Model: Relevance Assessment
        Input: Query and a list of snippets with IDs.
        Returns: List of IDs (integers) that are relevant and worth deep crawling.
        """
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        system_prompt = RELEVANCE_ASSESSMENT_PROMPT.format(current_time=current_time)
        
        user_message = f"Query: {query}\n\nSnippets:\n"
        for item in snippets:
            date_info = f"Date: {item.get('date', 'N/A')}\n" if item.get('date') else ""
            user_message += f"ID [{item['id']}]: Title: {item['title']}\n{date_info}Snippet: {item['snippet']}\n\n"

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ]
            )
            content = response.choices[0].message.content
            
            data = self._extract_json(content)
            if data:
                return data.get("relevant_ids", [])
            
            return [s['id'] for s in snippets[:3]]
        except Exception as e:
            print(f"Error in assess_relevance: {e}")
            # Fallback: return top 3 if parsing fails
            return [s['id'] for s in snippets[:3]]

    async def decide_click_elements(self, query: str, elements: List[Dict]) -> List[int]:
        """
        [New] AI Model: Decide which elements to click
        Input: Query and a list of interactive elements (id, text, type).
        Returns: List of IDs to click.
        """
        if not elements:
            return []
            
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        system_prompt = CLICK_DECISION_PROMPT.format(current_time=current_time)
        
        user_message = f"Query: {query}\n\nClickable Elements:\n"
        # Limit elements to avoid token overflow
        for el in elements[:50]:
            user_message += f"ID [{el['id']}]: [{el['tag']}] {el['text'][:100]}\n"
            
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ]
            )
            content = response.choices[0].message.content
            
            data = self._extract_json(content)
            if data:
                return data.get("clicked_ids", [])
            return []
        except Exception as e:
            print(f"Error in decide_click_elements: {e}")
            return []

    async def generate_answer(self, query: str, sources: List[Dict], history: Optional[List[Dict[str, str]]] = None, stream_callback: Optional[Callable[[str], None]] = None) -> Dict[str, any]:
        """
        [09] AI Model: Generation & Evaluation
        Input: Query and full content of selected sources.
        Returns: {"status": "sufficient"|"insufficient", "answer": "..."}
        """
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        system_prompt = ANSWER_GENERATION_PROMPT.format(current_time=current_time)
        
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add conversation history context if available
        if history:
             # We only take the last few turns
            recent_history = history[-6:]
            for msg in recent_history:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                messages.append({"role": role, "content": content})
        
        user_message = f"Question: {query}\n\nSources:\n"
        for src in sources:
            # Add strict length limit per source context to avoid token overflow
            content_preview = src['content'][:5000] 
            date_info = f" (Date: {src.get('date')})" if src.get('date') else ""
            user_message += f"Source [{src['id']}] (Title: {src['title']}{date_info}):\n{content_preview}\n\n"

        messages.append({"role": "user", "content": user_message})

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True
            )
            
            full_content = ""
            status = "sufficient" # Default assumption
            parsing_header = True
            header_buffer = ""
            answer_started = False
            
            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_content += content
                    
                    if parsing_header:
                        header_buffer += content
                        # Check for Status
                        if "Status:" in header_buffer and "\n" in header_buffer.split("Status:")[1]:
                             status_line = [line for line in header_buffer.split("\n") if "Status:" in line][0]
                             if "insufficient" in status_line.lower():
                                 status = "insufficient"
                        
                        # Check for Answer start
                        if "Answer:" in header_buffer:
                            parts = header_buffer.split("Answer:", 1)
                            pre_answer = parts[0]
                            # If we have content after Answer:, that's the start of the answer
                            if len(parts) > 1:
                                answer_chunk = parts[1]
                                parsing_header = False
                                answer_started = True
                                if status == "sufficient" and stream_callback and answer_chunk:
                                    stream_callback(answer_chunk)
                        
                        # Safety valve: if buffer gets too long without Answer:, maybe model didn't follow format
                        if len(header_buffer) > 500 and not answer_started:
                            parsing_header = False
                            # Assume whole thing is answer if status check passed or failed
                            if stream_callback:
                                stream_callback(header_buffer)

                    else:
                        # Streaming answer
                        if status == "sufficient" and stream_callback:
                            stream_callback(content)

            # Post-processing to extract clean answer from full_content
            final_answer = full_content
            if "Answer:" in full_content:
                final_answer = full_content.split("Answer:", 1)[1].strip()
            elif "Status:" in full_content:
                 # Fallback if Answer: tag missing but Status present
                 lines = full_content.split("\n")
                 # Filter out metadata lines
                 final_answer = "\n".join([l for l in lines if not l.startswith("Status:") and not l.startswith("Missing_Info:")])
            
            return {"status": status, "answer": final_answer.strip()}

        except Exception as e:
            print(f"Error in generate_answer: {e}")
            return {"status": "sufficient", "answer": f"生成答案时出错: {e}"}