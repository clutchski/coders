#!/usr/bin/env python3
import argparse
import subprocess
import shutil
import os
import re
import json
import csv
import sys
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
        # Update existing repository
        update_result = subprocess.run(
            ['git', 'fetch', '--all', '--quiet'],
            cwd=repo_cache_path,
            capture_output=True, text=True
        )
        if update_result.returncode != 0:
            shutil.rmtree(repo_cache_path)
            clone_result = subprocess.run(
                ['git', 'clone', '--quiet', repo_url, repo_cache_path],
                capture_output=True, text=True
            )
            if clone_result.returncode != 0:
                raise RuntimeError(f"Git clone failed: {clone_result.stderr}")
    else:
        clone_result = subprocess.run(
            ['git', 'clone', '--quiet', repo_url, repo_cache_path],
            capture_output=True, text=True
        )
        if clone_result.returncode != 0:
            raise RuntimeError(f"Git clone failed: {clone_result.stderr}")

    return repo_cache_path


def get_commit_email_stats(repo_path):
    """Extract commit statistics by email from git log."""
    cmd = ['git', 'log', '--format=%an|%ae|%H', '--all']
    result = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"Git log failed: {result.stderr}")

    email_stats = defaultdict(lambda: {'commits': 0, 'sha': '', 'name': ''})

    for line in result.stdout.strip().split('\n'):
        line = line.strip()
        if '|' in line and line.count('|') >= 2:
            name, email, sha = line.split('|', 2)
            name = name.strip()
            email = email.strip()
            sha = sha.strip()
            email_stats[email]['commits'] += 1
            if not email_stats[email]['sha']:  # Store first SHA we encounter
                email_stats[email]['sha'] = sha
            if not email_stats[email]['name']:  # Store first name we encounter
                email_stats[email]['name'] = name

    return email_stats

def get_github_contributors(repo_url, token=None):
    """Get GitHub contributors with minimal API calls."""
    owner, repo = parse_github_url(repo_url)
    if token:
        g = Github(token)
    else:
        g = Github()
    
    repository = g.get_repo(f"{owner}/{repo}")
    contributors = list(repository.get_contributors())
    
    # Create login to profile mapping (email not available without extra API calls)
    login_to_profile = {}
    for contributor in contributors:
        login_to_profile[contributor.login] = contributor.html_url
    
    return login_to_profile, repository, g

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
    cache_key = f"commit_{sha}"
    if cache_key in cache:
        return cache[cache_key]
    
    commit = repository.get_commit(sha)
    
    result = {'profile_url': '', 'name': '', 'linkedin': '', 'website': '', 'company': ''}
    if commit.author:
        result['profile_url'] = commit.author.html_url
        result['name'] = commit.author.name or commit.author.login
    
    cache[cache_key] = result
    return result

def parse_blog_url(blog_url):
    """Parse blog URL to separate LinkedIn, personal website, etc."""
    if not blog_url:
        return '', ''
    
    blog_url = blog_url.strip()
    if 'linkedin.com' in blog_url.lower():
        return blog_url, ''
    else:
        return '', blog_url

def lookup_user_details(github_client, login, cache):
    """Lookup full user details including blog/company with caching."""
    cache_key = f"user_{login}"
    if cache_key in cache:
        return cache[cache_key]
    
    user = github_client.get_user(login)
    
    blog_url = user.blog or ''
    
    linkedin, website = parse_blog_url(blog_url)
    
    result = {
        'profile_url': user.html_url,
        'name': user.name or user.login,
        'linkedin': linkedin,
        'website': website,
        'company': user.company or ''
    }
    
    cache[cache_key] = result
    return result

def main():
    parser = argparse.ArgumentParser(description='Get commit statistics by email from repository')
    parser.add_argument('repo_url', help='GitHub repository URL')
    parser.add_argument('--token', help='GitHub personal access token')
    parser.add_argument('--limit', type=int, help='Limit number of results (default: all)')
    parser.add_argument('--min-commits', type=int, default=1, help='Minimum commits required (default: 1)')
    parser.add_argument('--include-details', action='store_true', help='Include blog/company (requires extra API calls)')

    args = parser.parse_args()

    # Set up cache directory
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cache')
    os.makedirs(cache_dir, exist_ok=True)

    # Clone or update repository
    repo_cache_path = clone_or_update_repo(args.repo_url, cache_dir)

    # Get commit statistics by email
    email_stats = get_commit_email_stats(repo_cache_path)

    # Get GitHub contributor profiles
    token = args.token or os.getenv('GITHUB_TOKEN')
    login_to_profile, repository, github_client = get_github_contributors(args.repo_url, token)

    # Load profile cache
    profile_cache = load_profile_cache(cache_dir)

    # Filter by minimum commits, sort by commit count descending and limit results
    filtered_emails = [(email, stats) for email, stats in email_stats.items() if stats['commits'] >= args.min_commits]
    sorted_emails = sorted(filtered_emails, key=lambda x: x[1]['commits'], reverse=True)
    if args.limit:
        sorted_emails = sorted_emails[:args.limit]
    
    writer = csv.writer(sys.stdout)
    if args.include_details:
        writer.writerow(['name', 'email', 'commits', 'sample_sha', 'github_profile', 'linkedin', 'website', 'company'])
    else:
        writer.writerow(['email', 'commits', 'sample_sha', 'github_profile'])
    
    for email, stats in sorted_emails:
        # Try to match email to GitHub login (simple heuristic)
        user_details = None
        email_user = email.split('@')[0].lower()
        for login, profile_url in login_to_profile.items():
            if login.lower() == email_user:
                if args.include_details:
                    user_details = lookup_user_details(github_client, login, profile_cache)
                else:
                    user_details = {'profile_url': profile_url, 'name': '', 'linkedin': '', 'website': '', 'company': ''}
                break
        
        # If no match found, lookup from commit
        if not user_details:
            user_details = lookup_profile_from_commit(repository, stats['sha'], profile_cache)
            
            # If we found a commit author and want details, get them
            if args.include_details and user_details['profile_url']:
                login = user_details['profile_url'].split('/')[-1]
                user_details = lookup_user_details(github_client, login, profile_cache)
        
        if args.include_details:
            writer.writerow([
                stats['name'],
                email.strip(), 
                stats['commits'], 
                stats['sha'].strip(), 
                user_details.get('profile_url', ''),
                user_details.get('linkedin', ''),
                user_details.get('website', ''),
                user_details.get('company', '')
            ])
        else:
            writer.writerow([email.strip(), stats['commits'], stats['sha'].strip(), user_details.get('profile_url', '')])

    # Save updated cache
    save_profile_cache(cache_dir, profile_cache)


if __name__ == "__main__":
    main()
