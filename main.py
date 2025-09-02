#!/usr/bin/env python3
import argparse
import subprocess
import shutil
import os
import re
import json
from collections import defaultdict
from github import Github


def parse_github_url(url):
    """Extract owner and repo name from GitHub URL."""
    # Handle HTTPS URLs
    https_match = re.match(r'https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$', url)
    if https_match:
        return https_match.group(1), https_match.group(2)

    # Handle SSH URLs
    ssh_match = re.match(r'git@github\.com:([^/]+)/(.+?)(?:\.git)?$', url)
    if ssh_match:
        return ssh_match.group(1), ssh_match.group(2)

    raise ValueError(f"Invalid GitHub URL format: {url}")


def clone_or_update_repo(repo_url, cache_dir):
    """Clone repository to cache directory or update if exists."""
    # Parse repo URL to get cache path
    owner, repo = parse_github_url(repo_url)
    repo_cache_path = os.path.join(cache_dir, f"{owner}_{repo}")

    if os.path.exists(repo_cache_path):
        print(f"Updating cached repository at {repo_cache_path}...")
        # Update existing repository
        update_result = subprocess.run(
            ['git', 'fetch', '--all', '--quiet'],
            cwd=repo_cache_path,
            capture_output=True, text=True
        )
        if update_result.returncode != 0:
            print(f"Warning: Failed to update cache, re-cloning: {update_result.stderr}")
            shutil.rmtree(repo_cache_path)
            clone_result = subprocess.run(
                ['git', 'clone', '--quiet', repo_url, repo_cache_path],
                capture_output=True, text=True
            )
            if clone_result.returncode != 0:
                raise RuntimeError(f"Git clone failed: {clone_result.stderr}")
    else:
        print(f"Cloning repository to cache at {repo_cache_path}...")
        clone_result = subprocess.run(
            ['git', 'clone', '--quiet', repo_url, repo_cache_path],
            capture_output=True, text=True
        )
        if clone_result.returncode != 0:
            raise RuntimeError(f"Git clone failed: {clone_result.stderr}")

    return repo_cache_path


def get_commit_email_stats(repo_path):
    """Extract commit statistics by email from git log."""
    cmd = ['git', 'log', '--format=%ae|%H', '--all']
    result = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"Git log failed: {result.stderr}")

    email_stats = defaultdict(lambda: {'commits': 0, 'sha': ''})

    for line in result.stdout.strip().split('\n'):
        line = line.strip()
        if '|' in line:
            email, sha = line.split('|', 1)
            email = email.strip()
            sha = sha.strip()
            email_stats[email]['commits'] += 1
            if not email_stats[email]['sha']:  # Store first SHA we encounter
                email_stats[email]['sha'] = sha

    return email_stats

def get_github_contributors(repo_url, token=None):
    """Get GitHub contributors with minimal API calls."""
    owner, repo = parse_github_url(repo_url)
    if token:
        g = Github(token)
        print(f"Using GitHub token (rate limit: 5000/hour)")
    else:
        g = Github()
        print(f"No token - rate limited to 60/hour")
    
    repository = g.get_repo(f"{owner}/{repo}")
    contributors = list(repository.get_contributors())
    print(f"Found {len(contributors)} GitHub contributors")
    
    # Create login to profile mapping (email not available without extra API calls)
    login_to_profile = {}
    for contributor in contributors:
        login_to_profile[contributor.login] = contributor.html_url
    
    return login_to_profile, repository

def load_profile_cache(cache_dir):
    """Load cached profile lookups."""
    cache_file = os.path.join(cache_dir, 'profile_cache.json')
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            return json.load(f)
    return {}

def save_profile_cache(cache_dir, cache):
    """Save profile lookup cache."""
    cache_file = os.path.join(cache_dir, 'profile_cache.json')
    with open(cache_file, 'w') as f:
        json.dump(cache, f, indent=2)

def lookup_profile_from_commit(repository, sha, cache):
    """Lookup GitHub profile from commit SHA with caching."""
    if sha in cache:
        print(f"Using cached profile for {sha[:12]}")
        return cache[sha]
    
    print(f"Looking up GitHub profile for commit {sha[:12]}...")
    commit = repository.get_commit(sha)
    
    profile_url = ''
    if commit.author:
        profile_url = commit.author.html_url
    
    cache[sha] = profile_url
    return profile_url

def main():
    parser = argparse.ArgumentParser(description='Get commit statistics by email from repository')
    parser.add_argument('repo_url', help='GitHub repository URL')
    parser.add_argument('--token', help='GitHub personal access token')
    parser.add_argument('--limit', type=int, default=20, help='Limit number of results (default: 20)')

    args = parser.parse_args()

    # Set up cache directory
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cache')
    os.makedirs(cache_dir, exist_ok=True)

    # Clone or update repository
    repo_cache_path = clone_or_update_repo(args.repo_url, cache_dir)

    # Get commit statistics by email
    print("Analyzing commit history...")
    email_stats = get_commit_email_stats(repo_cache_path)

    # Get GitHub contributor profiles
    token = args.token or os.getenv('GITHUB_TOKEN')
    login_to_profile, repository = get_github_contributors(args.repo_url, token)

    # Load profile cache
    profile_cache = load_profile_cache(cache_dir)

    # Sort by commit count descending and limit results
    sorted_emails = sorted(email_stats.items(), key=lambda x: x[1]['commits'], reverse=True)[:args.limit]

    # Print results
    print(f"\nShowing top {len(sorted_emails)} commit emails:\n")
    print(f"{'Email':<40} {'Commits':<8} {'Sample SHA':<12} {'GitHub Profile'}")
    print("-" * 120)

    for email, stats in sorted_emails:
        # Try to match email to GitHub login (simple heuristic)
        profile = ''
        email_user = email.split('@')[0].lower()
        for login, profile_url in login_to_profile.items():
            if login.lower() == email_user:
                profile = profile_url
                break
        
        # If no match found, lookup from commit
        if not profile:
            profile = lookup_profile_from_commit(repository, stats['sha'], profile_cache)
        
        print(f"{email.strip():<40} {stats['commits']:<8} {stats['sha'].strip()[:12]:<12} {profile}")

    # Save updated cache
    save_profile_cache(cache_dir, profile_cache)


if __name__ == "__main__":
    main()
