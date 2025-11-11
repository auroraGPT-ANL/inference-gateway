# GitHub Pages Deployment Instructions

This guide explains how to enable and deploy the documentation to GitHub Pages.

## Prerequisites

- Admin access to the GitHub repository
- Documentation changes committed to the repository

## Step 1: Enable GitHub Pages

1. Go to your GitHub repository: `https://github.com/auroraGPT-ANL/inference-gateway`
2. Click on **Settings** (top navigation bar)
3. In the left sidebar, click on **Pages** (under "Code and automation")
4. Under **Source**, select:
   - Source: `GitHub Actions`

That's it! The workflow is already configured in `.github/workflows/deploy-docs.yml`.

## Step 2: Push to Main Branch

The documentation will automatically deploy when you push changes to the `main` branch:

```bash
git add .
git commit -m "docs: reorganize documentation structure"
git push origin main
```

The workflow will trigger automatically when:
- Files in `docs/` directory change
- `README.md` changes
- The workflow file itself changes

## Step 3: View Your Documentation

After the workflow completes (usually 1-2 minutes):

1. Go to **Settings** → **Pages**
2. You'll see a message: "Your site is live at `https://auroragpt-anl.github.io/inference-gateway/`"
3. Click the link to view your documentation

## Manual Deployment

You can also manually trigger the deployment:

1. Go to **Actions** tab in your repository
2. Click on "Deploy Documentation" workflow
3. Click **Run workflow** button
4. Select the `main` branch
5. Click **Run workflow**

## Workflow Details

The workflow (`.github/workflows/deploy-docs.yml`) does the following:

1. **Triggers on**:
   - Push to `main` branch (when docs files change)
   - Manual workflow dispatch

2. **Build Process**:
   - Checks out the repository
   - Copies documentation files to `_site` directory
   - Creates an HTML index page with navigation
   - Converts Markdown files to HTML using client-side rendering
   - Applies GitHub Markdown CSS for consistent styling

3. **Deployment**:
   - Uploads the built site as an artifact
   - Deploys to GitHub Pages

## Customization

### Change Branch

To deploy from a different branch, edit `.github/workflows/deploy-docs.yml`:

```yaml
on:
  push:
    branches:
      - main  # Change this to your preferred branch
```

### Add Custom Domain

1. Go to **Settings** → **Pages**
2. Under **Custom domain**, enter your domain (e.g., `docs.example.com`)
3. Add a `CNAME` file in the `docs/` directory with your domain:

```bash
echo "docs.example.com" > docs/CNAME
```

### Use a Documentation Framework

For more advanced features, consider using:

- **MkDocs**: Material theme, search, versioning
- **Sphinx**: API documentation, multiple formats
- **Jekyll**: Native GitHub Pages support, extensive themes
- **Docusaurus**: Modern React-based, built by Facebook

Example with MkDocs:

```yaml
# Add to .github/workflows/deploy-docs.yml
- name: Setup Python
  uses: actions/setup-python@v4
  with:
    python-version: '3.x'

- name: Install dependencies
  run: |
    pip install mkdocs-material

- name: Build with MkDocs
  run: mkdocs build
```

## Monitoring Deployments

### Check Deployment Status

1. Go to **Actions** tab
2. Click on the latest "Deploy Documentation" run
3. View logs to troubleshoot issues

### Common Issues

**404 Error**: 
- Ensure GitHub Pages is enabled in Settings
- Check that the workflow completed successfully
- Verify the source is set to "GitHub Actions"

**Changes Not Appearing**:
- Clear browser cache (Ctrl+Shift+R or Cmd+Shift+R)
- Wait a few minutes for CDN propagation
- Check the Actions tab for failed workflows

**Workflow Not Triggering**:
- Ensure the changed files match the `paths` filter in the workflow
- Check branch protection rules aren't blocking the workflow
- Verify GitHub Actions is enabled for your repository

## Viewing Deployment History

All deployments are tracked:

1. Go to **Settings** → **Pages**
2. Scroll down to see deployment history
3. Each deployment shows commit SHA and timestamp

## Rolling Back

To roll back to a previous version:

1. Find the commit with the working documentation
2. Create a new commit that reverts changes:

```bash
git revert <commit-hash>
git push origin main
```

Or checkout the old version:

```bash
git checkout <commit-hash> -- docs/
git commit -m "docs: rollback documentation"
git push origin main
```

## Security

The workflow uses:
- `permissions`: Limited to only what's needed
- `id-token: write`: For GitHub Pages deployment
- `contents: read`: For reading repository files

No secrets are required for basic deployment.

## Next Steps

After deploying:

1. Update README badges with documentation link
2. Add documentation link to repository description
3. Share documentation URL with users
4. Set up monitoring for broken links
5. Consider adding a search feature (available with MkDocs Material)

## Support

For issues with GitHub Pages deployment:
- [GitHub Pages Documentation](https://docs.github.com/en/pages)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Repository Issues](https://github.com/auroraGPT-ANL/inference-gateway/issues)

