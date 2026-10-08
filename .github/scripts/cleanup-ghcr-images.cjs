// Remove old GHCR versions using the GitHub Actions client and job summary.
module.exports = async ({ github, context, core }) => {
  const owner = context.repo.owner;
  const packageName = context.repo.repo.toLowerCase();
  const dryRun = process.env.DRY_RUN === 'true';
  const shaTag = /^sha-[0-9a-f]{7,64}$/i;

  const { data: account } = await github.rest.users.getByUsername({ username: owner });
  const organization = account.type === 'Organization';
  const route = organization
    ? '/orgs/{org}/packages/{package_type}/{package_name}/versions'
    : '/users/{username}/packages/{package_type}/{package_name}/versions';
  const params = {
    ...(organization ? { org: owner } : { username: owner }),
    package_type: 'container',
    package_name: packageName,
  };
  const versions = await github.paginate(`GET ${route}`, { ...params, per_page: 100 });
  const tags = version => version.metadata?.container?.tags ?? [];
  const shaVersions = versions
    .filter(version => tags(version).length > 0 &&
      tags(version).every(tag => shaTag.test(tag)))
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  const candidates = [
    ...versions.filter(version => tags(version).length === 0),
    ...shaVersions.slice(5),
  ];

  await core.summary
    .addHeading(`GHCR cleanup${dryRun ? ' (dry run)' : ''}`)
    .addRaw(`Package: ${owner}/${packageName}. Keep newest 5 SHA-only versions.\n`)
    .addTable([
      ['Version ID', 'Tags', 'Created'],
      ...candidates.map(version => [
        String(version.id), tags(version).join(', ') || '(untagged)', version.created_at,
      ]),
    ])
    .write();

  for (const version of candidates) {
    core.info(`${dryRun ? 'Would delete' : 'Deleting'} version ${version.id}`);
    if (!dryRun) {
      await github.request(`DELETE ${route}/{package_version_id}`, {
        ...params, package_version_id: version.id,
      });
    }
  }
};
