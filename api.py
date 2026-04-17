from curl_cffi import requests
from config import get_config
from auth import get_auth_cookies, get_auth_headers

GRAPHQL_URL = "https://leetcode.com/graphql"
BASE_URL = "https://leetcode.com"

def _graphql_request(query, variables=None):
    headers = get_auth_headers()
    cookies = get_auth_cookies()
    
    payload = {
        "query": query,
        "variables": variables or {}
    }
    
    response = requests.post(
        GRAPHQL_URL, 
        json=payload, 
        headers=headers, 
        cookies=cookies,
        impersonate="chrome"
    )
    response.raise_for_status()
    return response.json()

def get_questions_list(skip=0, limit=100, filters=None):
    query = """
    query problemsetQuestionList($categorySlug: String, $limit: Int, $skip: Int, $filters: QuestionListFilterInput) {
      problemsetQuestionList: questionList(
        categorySlug: $categorySlug
        limit: $limit
        skip: $skip
        filters: $filters
      ) {
        total: totalNum
        questions: data {
          acRate
          difficulty
          frontendQuestionId: questionFrontendId
          title
          titleSlug
          topicTags {
            name
            id
            slug
          }
        }
      }
    }
    """
    variables = {
        "categorySlug": "",
        "skip": skip,
        "limit": limit,
        "filters": filters or {}
    }
    data = _graphql_request(query, variables)
    return data.get("data", {}).get("problemsetQuestionList", {})

def get_question_detail(title_slug: str):
    query = """
    query getQuestionDetail($titleSlug: String!) {
      question(titleSlug: $titleSlug) {
        questionId
        questionFrontendId
        title
        titleSlug
        content
        exampleTestcases
        difficulty
        topicTags {
          name
        }
        codeSnippets {
          lang
          langSlug
          code
        }
      }
    }
    """
    variables = {"titleSlug": title_slug}
    data = _graphql_request(query, variables)
    return data.get("data", {}).get("question")

def get_tags():
    # LeetCode's topicTag query frequently changes or is restricted, so we provide a robust static list
    # of the most popular tags which can be used for filtering.
    return [
        {"name": "Array", "slug": "array"},
        {"name": "String", "slug": "string"},
        {"name": "Hash Table", "slug": "hash-table"},
        {"name": "Dynamic Programming", "slug": "dynamic-programming"},
        {"name": "Math", "slug": "math"},
        {"name": "Sorting", "slug": "sorting"},
        {"name": "Greedy", "slug": "greedy"},
        {"name": "Depth-First Search", "slug": "depth-first-search"},
        {"name": "Binary Search", "slug": "binary-search"},
        {"name": "Tree", "slug": "tree"},
        {"name": "Breadth-First Search", "slug": "breadth-first-search"},
        {"name": "Matrix", "slug": "matrix"},
        {"name": "Two Pointers", "slug": "two-pointers"},
        {"name": "Bit Manipulation", "slug": "bit-manipulation"},
        {"name": "Binary Tree", "slug": "binary-tree"},
        {"name": "Heap (Priority Queue)", "slug": "heap-priority-queue"},
        {"name": "Stack", "slug": "stack"},
        {"name": "Graph", "slug": "graph"},
        {"name": "Design", "slug": "design"},
        {"name": "Simulation", "slug": "simulation"},
        {"name": "Prefix Sum", "slug": "prefix-sum"},
        {"name": "Counting", "slug": "counting"},
        {"name": "Backtracking", "slug": "backtracking"},
        {"name": "Sliding Window", "slug": "sliding-window"},
        {"name": "Union Find", "slug": "union-find"},
        {"name": "Linked List", "slug": "linked-list"},
        {"name": "Memoization", "slug": "memoization"},
        {"name": "Topological Sort", "slug": "topological-sort"},
        {"name": "Trie", "slug": "trie"},
        {"name": "Divide and Conquer", "slug": "divide-and-conquer"},
    ]

def test_code(title_slug: str, question_id: str, lang: str, typed_code: str, data_input: str):
    url = f"{BASE_URL}/problems/{title_slug}/interpret_solution/"
    headers = get_auth_headers()
    headers["Referer"] = f"https://leetcode.com/problems/{title_slug}/"
    headers["Origin"] = "https://leetcode.com"
    cookies = get_auth_cookies()
    
    payload = {
        "lang": lang,
        "question_id": question_id,
        "typed_code": typed_code,
        "data_input": data_input
    }
    
    response = requests.post(url, json=payload, headers=headers, cookies=cookies, impersonate="chrome")
    if response.status_code != 200:
        raise Exception(f"HTTP {response.status_code}: {response.text[:500]}")
    return response.json()

def submit_code(title_slug: str, question_id: str, lang: str, typed_code: str):
    url = f"{BASE_URL}/problems/{title_slug}/submit/"
    headers = get_auth_headers()
    headers["Referer"] = f"https://leetcode.com/problems/{title_slug}/"
    headers["Origin"] = "https://leetcode.com"
    cookies = get_auth_cookies()
    
    payload = {
        "lang": lang,
        "question_id": question_id,
        "typed_code": typed_code
    }
    
    response = requests.post(url, json=payload, headers=headers, cookies=cookies, impersonate="chrome")
    if response.status_code != 200:
        raise Exception(f"HTTP {response.status_code}: {response.text[:500]}")
    return response.json()

def check_submission(submission_id: int):
    url = f"{BASE_URL}/submissions/detail/{submission_id}/check/"
    headers = get_auth_headers()
    cookies = get_auth_cookies()
    
    response = requests.get(url, headers=headers, cookies=cookies, impersonate="chrome")
    response.raise_for_status()
    return response.json()
