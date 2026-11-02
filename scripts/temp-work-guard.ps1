# 临时工作目录清理守卫：只允许删除「系统临时目录下、带指定前缀」的目录。
#
# 存在的理由：CI 上 [IO.Path]::GetTempPath() 可能返回 8.3 短路径（RUNNER~1），
# 而 $env:TEMP 是长路径，直接做字符串比较会误报"拒绝清理"。
# 因此两边都必须先规范化成长路径再比较。
if (-not ('MediaForge.TempGuardNative' -as [type])) {
    Add-Type -Namespace MediaForge -Name TempGuardNative -MemberDefinition '[DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern int GetLongPathName(string shortPath, System.Text.StringBuilder longPath, int buffer);'
}

function Get-LongPath([string]$Path) {
    $builder = New-Object System.Text.StringBuilder 2048
    [void][MediaForge.TempGuardNative]::GetLongPathName($Path, $builder, $builder.Capacity)
    if ($builder.Length -eq 0) { return $Path }
    return $builder.ToString()
}

function Assert-TempWorkDir {
    param(
        [Parameter(Mandatory = $true)][string]$Work,
        [Parameter(Mandatory = $true)][string]$Prefix
    )
    $name = Split-Path -Leaf $Work
    if (-not $name.StartsWith($Prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理意外目录名：$Work"
    }
    $parent = Get-LongPath ([System.IO.Path]::GetDirectoryName($Work)).TrimEnd('\', '/')
    $tempRoot = Get-LongPath ([System.IO.Path]::GetFullPath($env:TEMP)).TrimEnd('\', '/')
    if (-not [string]::Equals($parent, $tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理临时目录之外的路径：$Work（父目录 $parent，临时根 $tempRoot）"
    }
    return (Resolve-Path $Work).Path
}
